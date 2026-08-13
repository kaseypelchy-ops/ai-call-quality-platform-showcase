"""
Duplicate-safe coaching notification handler.

Simplified public example based on the production notification pipeline.

The completion marker is emitted only after analysis persistence succeeds.
Eventarc may deliver the same marker event more than once, so a durable GCS
lock is created atomically before SMTP is contacted.
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage

import functions_framework
from google.api_core import exceptions
from google.cloud import storage


RECEIPT_BUCKET = os.environ["NOTIFICATION_RECEIPT_BUCKET"]
SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]
SMTP_FROM = os.environ["SMTP_FROM"]


def send_email(
    recipient: str,
    subject: str,
    body: str,
) -> tuple[str, str]:
    message = EmailMessage()
    message["From"] = SMTP_FROM
    message["To"] = recipient
    message["Subject"] = subject
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(body)

    context = ssl.create_default_context()

    with smtplib.SMTP(
        SMTP_HOST,
        SMTP_PORT,
        timeout=30,
    ) as server:
        server.starttls(context=context)
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(message)

    return (
        message.get("Message-ID", ""),
        datetime.now(timezone.utc).isoformat(),
    )


def acquire_notification_lock(
    bucket: storage.Bucket,
    *,
    call_key: str,
    recipient: str,
) -> tuple[bool, storage.Blob]:
    lock_name = f"locks/{call_key}.json"
    lock = bucket.blob(lock_name)

    payload = {
        "call_key": call_key,
        "recipient": recipient,
        "status": "sending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        lock.upload_from_string(
            json.dumps(payload),
            content_type="application/json",
            if_generation_match=0,
        )
        return True, lock
    except exceptions.PreconditionFailed:
        # A permanent successful lock makes duplicate Eventarc deliveries safe.
        return False, lock


@functions_framework.cloud_event
def send_coaching_notification(cloud_event):
    event = cloud_event.data or {}

    source_bucket = str(event.get("bucket") or "")
    source_name = str(event.get("name") or "")

    storage_client = storage.Client()
    marker_blob = storage_client.bucket(source_bucket).blob(source_name)

    marker = json.loads(
        marker_blob.download_as_text()
    )

    call_key = str(marker["call_key"])
    recipient = str(marker.get("recipient") or "").strip().lower()

    receipt_bucket = storage_client.bucket(RECEIPT_BUCKET)

    # Integrity / policy gates are persisted by the worker before this stage.
    if not marker.get("email_delivery_allowed", False):
        receipt_bucket.blob(
            f"receipts/{call_key}.json"
        ).upload_from_string(
            json.dumps({
                "status": "skipped",
                "reason": marker.get("email_block_reason"),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }),
            content_type="application/json",
        )
        return

    acquired, lock = acquire_notification_lock(
        receipt_bucket,
        call_key=call_key,
        recipient=recipient,
    )

    if not acquired:
        return

    if not recipient or "@" not in recipient:
        lock.upload_from_string(
            json.dumps({
                "call_key": call_key,
                "status": "skipped",
                "reason": "missing_recipient",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }),
            content_type="application/json",
        )
        return

    subject = "New call coaching feedback is available"
    body = """
A reviewed customer interaction is now available in your coaching portal.

Sign in to review the call summary, strengths, and coaching opportunities.
""".strip()

    try:
        message_id, sent_at = send_email(
            recipient,
            subject,
            body,
        )
    except Exception:
        # SMTP did not report success. Remove the lock so Eventarc can retry.
        #
        # If a provider can accept a caller-supplied idempotency key, that is
        # preferable for strict exactly-once semantics.
        try:
            lock.delete()
        finally:
            raise

    success = {
        "call_key": call_key,
        "recipient": recipient,
        "status": "sent",
        "provider_message_id": message_id,
        "completed_at": sent_at,
    }

    # Keep the successful lock permanently. If a later database status write
    # fails, a retry must still NOT send another message.
    lock.upload_from_string(
        json.dumps(success),
        content_type="application/json",
    )

    receipt_bucket.blob(
        f"receipts/{call_key}.json"
    ).upload_from_string(
        json.dumps(success),
        content_type="application/json",
    )
