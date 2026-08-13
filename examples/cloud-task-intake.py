"""
Deterministic Cloud Tasks intake for call-analysis workloads.

Simplified public example based on the production QA platform.

Cloud Storage / Eventarc delivery is at-least-once. A deterministic task name
turns repeated finalize events for the same immutable object generation into
harmless duplicate-create attempts.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

import functions_framework
from google.api_core import exceptions
from google.cloud import storage, tasks_v2
from google.protobuf import duration_pb2


PROJECT_ID = os.environ["PROJECT_ID"]
INPUT_BUCKET = os.environ["INPUT_BUCKET"]
QUEUE_ID = os.environ["ANALYSIS_QUEUE_ID"]
QUEUE_LOCATION = os.environ.get("QUEUE_LOCATION", "us-central1")
WORKER_URL = os.environ["ANALYSIS_WORKER_URL"]
TASK_SERVICE_ACCOUNT = os.environ["TASK_SERVICE_ACCOUNT"]

SUPPORTED_AUDIO = {"mp3", "wav", "m4a", "aac", "flac", "ogg"}


def extension(name: str) -> str:
    return name.lower().rsplit(".", 1)[-1] if "." in name else ""


def metadata_name(audio_name: str) -> str:
    return audio_name.rsplit(".", 1)[0] + ".json"


def deterministic_task_id(
    bucket: str,
    object_name: str,
    generation: Any,
) -> str:
    """
    Include the immutable GCS generation in the queue identity.

    The same Storage event will resolve to the same Cloud Task name.
    A genuinely new object generation can still be processed independently.
    """
    raw = f"{bucket}\n{object_name}\n{generation or ''}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:40]
    return f"call-{digest}"


def create_analysis_task(
    *,
    bucket: str,
    object_name: str,
    generation: Any,
    metadata_generation: Any,
) -> str:
    client = tasks_v2.CloudTasksClient()

    parent = client.queue_path(
        PROJECT_ID,
        QUEUE_LOCATION,
        QUEUE_ID,
    )

    task_name = client.task_path(
        PROJECT_ID,
        QUEUE_LOCATION,
        QUEUE_ID,
        deterministic_task_id(bucket, object_name, generation),
    )

    payload = {
        "bucket": bucket,
        "name": object_name,
        "generation": str(generation or ""),
        "metadata_generation": str(metadata_generation or ""),
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }

    task = tasks_v2.Task(
        name=task_name,
        http_request=tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST,
            url=WORKER_URL,
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload).encode("utf-8"),
            oidc_token=tasks_v2.OidcToken(
                service_account_email=TASK_SERVICE_ACCOUNT,
                audience=WORKER_URL,
            ),
        ),
    )

    deadline = duration_pb2.Duration()
    deadline.FromSeconds(1800)
    task.dispatch_deadline = deadline

    return client.create_task(
        parent=parent,
        task=task,
    ).name


@functions_framework.cloud_event
def queue_call_for_analysis(cloud_event):
    data = cloud_event.data or {}

    bucket_name = str(data.get("bucket") or "").strip()
    object_name = str(data.get("name") or "").strip()
    generation = data.get("generation")

    if bucket_name != INPUT_BUCKET:
        return

    if not object_name or extension(object_name) not in SUPPORTED_AUDIO:
        return

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    audio = bucket.blob(
        object_name,
        generation=int(generation) if generation else None,
    )

    if not audio.exists():
        return

    # The importer writes metadata next to the audio. Raising here is
    # intentional: Eventarc can retry while the companion object finishes.
    meta = bucket.blob(metadata_name(object_name))

    if not meta.exists():
        raise RuntimeError(
            f"Companion metadata is not available yet for {object_name}."
        )

    meta.reload()

    try:
        create_analysis_task(
            bucket=bucket_name,
            object_name=object_name,
            generation=generation,
            metadata_generation=meta.generation,
        )
    except exceptions.AlreadyExists:
        # At-least-once Storage delivery becomes a no-op.
        return
