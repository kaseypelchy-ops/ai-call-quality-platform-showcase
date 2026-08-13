"""
Queue-driven structured QA analysis worker.

Simplified public example based on the production platform.

Important design boundaries:
- The model does not choose the employee identity.
- Eligibility is checked before model use.
- Duplicate work is rejected before model use.
- The model returns structured evidence/scores.
- Application code calculates the official weighted score.
- Persistence succeeds before a notification-ready marker is published.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from google.api_core import exceptions
from google.cloud import storage
from google import genai
from google.genai import types


LOCK_BUCKET = os.environ["PROCESSING_LOCK_BUCKET"]
ARCHIVE_BUCKET = os.environ["PRIVATE_ARCHIVE_BUCKET"]
COMPLETION_BUCKET = os.environ["COMPLETION_MARKER_BUCKET"]
MODEL_ID = os.environ["MODEL_ID"]

# Public example weights only. Production rubric details are private.
CATEGORY_WEIGHTS = {
    "communication": 20,
    "discovery": 20,
    "accuracy": 25,
    "resolution": 25,
    "closing": 10,
}


QA_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "call_summary": {"type": "STRING"},
        "grading_eligibility": {
            "type": "OBJECT",
            "properties": {
                "status": {
                    "type": "STRING",
                    "enum": ["gradable", "not_gradable"],
                },
                "reason": {"type": "STRING"},
                "confidence": {
                    "type": "STRING",
                    "enum": ["high", "medium", "low"],
                },
            },
            "required": ["status", "reason", "confidence"],
        },
        "category_scores": {
            "type": "OBJECT",
            "properties": {
                name: {
                    "type": "NUMBER",
                    "minimum": 1,
                    "maximum": 5,
                }
                for name in CATEGORY_WEIGHTS
            },
            "required": list(CATEGORY_WEIGHTS),
        },
        "strengths": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
        "coaching_opportunities": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
        "evidence": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "category": {"type": "STRING"},
                    "timestamp_seconds": {"type": "NUMBER"},
                    "observation": {"type": "STRING"},
                },
                "required": [
                    "category",
                    "timestamp_seconds",
                    "observation",
                ],
            },
        },
    },
    "required": [
        "call_summary",
        "grading_eligibility",
        "category_scores",
        "strengths",
        "coaching_opportunities",
        "evidence",
    ],
}


def clamp(value: Any, minimum: float, maximum: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = minimum

    return max(minimum, min(maximum, numeric))


def calculate_official_score(category_scores: dict[str, Any]) -> float:
    """
    The LLM supplies category-level observations.

    The application owns the official aggregate calculation so weighting and
    thresholds remain deterministic and versionable outside the prompt.
    """
    weighted_total = 0.0

    for category, weight in CATEGORY_WEIGHTS.items():
        score_1_to_5 = clamp(category_scores.get(category), 1.0, 5.0)
        weighted_total += (score_1_to_5 / 5.0) * weight

    return round(weighted_total, 1)


def validate_structured_analysis(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Model output must be a JSON object.")

    eligibility = data.get("grading_eligibility")
    if not isinstance(eligibility, dict):
        raise ValueError("Missing grading eligibility.")

    scores = data.get("category_scores")
    if not isinstance(scores, dict):
        raise ValueError("Missing category scores.")

    missing = [
        category
        for category in CATEGORY_WEIGHTS
        if category not in scores
    ]

    if missing:
        raise ValueError(
            f"Structured analysis omitted categories: {', '.join(missing)}"
        )

    return data


def acquire_processing_lock(
    storage_client: storage.Client,
    call_key: str,
) -> tuple[bool, str]:
    """
    GCS object creation with if_generation_match=0 is atomic.

    Only one worker can create the lock. A duplicate Cloud Task cannot start a
    second model request for the same call.
    """
    lock_name = f"analysis-locks/{call_key}.json"
    blob = storage_client.bucket(LOCK_BUCKET).blob(lock_name)

    payload = {
        "status": "processing",
        "call_key": call_key,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        blob.upload_from_string(
            json.dumps(payload),
            content_type="application/json",
            if_generation_match=0,
        )
        return True, lock_name
    except exceptions.PreconditionFailed:
        return False, lock_name


def archive_immutable_recording(
    storage_client: storage.Client,
    *,
    source_bucket: str,
    source_name: str,
    source_generation: int,
    call_key: str,
) -> str:
    source = storage_client.bucket(source_bucket).blob(
        source_name,
        generation=source_generation,
    )

    if not source.exists():
        raise FileNotFoundError("Source generation no longer exists.")

    target_name = f"calls/{call_key}/recording{os.path.splitext(source_name)[1]}"
    target = storage_client.bucket(ARCHIVE_BUCKET).blob(target_name)

    # A public example uses download/upload for clarity. Production systems may
    # prefer generation-aware copy/rewrite depending on object size.
    target.upload_from_string(
        source.download_as_bytes(),
        content_type=source.content_type or "application/octet-stream",
    )

    return f"gs://{ARCHIVE_BUCKET}/{target_name}"


def analyze_recording(
    *,
    recording_uri: str,
    mime_type: str,
    rubric_context: dict[str, Any],
) -> dict[str, Any]:
    client = genai.Client(vertexai=True)

    prompt = """
Evaluate the target support representative's interaction.

Return only structured JSON matching the supplied schema.
Use evidence from the target representative only.
Do not invent timestamps.
Do not make employment or disciplinary decisions.
The application will calculate the official aggregate score.
""".strip()

    response = client.models.generate_content(
        model=MODEL_ID,
        contents=[
            types.Part.from_uri(
                file_uri=recording_uri,
                mime_type=mime_type,
            ),
            json.dumps(rubric_context),
            prompt,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=QA_RESPONSE_SCHEMA,
            temperature=0.1,
        ),
    )

    if not response.text:
        raise RuntimeError("The model returned an empty response.")

    return validate_structured_analysis(
        json.loads(response.text)
    )


def process_call(
    *,
    payload: dict[str, Any],
    repository,
    rubric_provider,
) -> dict[str, Any]:
    """
    repository and rubric_provider are injected here so the public example can
    show orchestration without exposing the production database implementation.
    """
    storage_client = storage.Client()

    bucket_name = str(payload["bucket"])
    object_name = str(payload["name"])
    generation = int(payload["generation"])

    call_key = repository.source_call_key(
        bucket_name,
        object_name,
        generation,
    )

    # Fast duplicate check before archive, lock acquisition, or model spend.
    existing = repository.find_existing_call(
        bucket_name=bucket_name,
        source_name=object_name,
    )

    if existing:
        return {
            "status": "skipped_duplicate",
            "call_id": existing["id"],
        }

    metadata = repository.load_metadata(payload)

    # Employee identity must come from authoritative application data.
    external_agent_id = str(metadata.get("agent_id") or "").strip()
    agent = repository.find_active_agent(external_agent_id)

    if not agent:
        return {
            "status": "skipped_ineligible",
            "reason": "agent_not_active_or_not_found",
        }

    rubric = rubric_provider.for_team(agent["team_id"])

    acquired, lock_name = acquire_processing_lock(
        storage_client,
        call_key,
    )

    if not acquired:
        return {
            "status": "duplicate_in_progress_or_complete",
        }

    try:
        recording_uri = archive_immutable_recording(
            storage_client,
            source_bucket=bucket_name,
            source_name=object_name,
            source_generation=generation,
            call_key=call_key,
        )

        # A durable structured checkpoint allows downstream retries to continue
        # without paying for a second model request.
        checkpoint = repository.load_analysis_checkpoint(call_key)

        if checkpoint:
            analysis = checkpoint
        else:
            analysis = analyze_recording(
                recording_uri=recording_uri,
                mime_type=repository.mime_type_for(object_name),
                rubric_context=rubric.public_model_context(),
            )

            repository.save_analysis_checkpoint(
                call_key,
                analysis,
            )

        eligibility = analysis["grading_eligibility"]
        official_score = None

        if eligibility["status"] == "gradable":
            official_score = calculate_official_score(
                analysis["category_scores"]
            )

        persisted = repository.upsert_call({
            "source_key": call_key,
            "source_bucket": bucket_name,
            "source_file": object_name,
            "source_generation": generation,
            "agent_external_id": agent["external_id"],
            "agent_name": agent["display_name"],
            "team_id": agent["team_id"],
            "rubric_version": rubric.version,
            "analysis": analysis,
            "overall_score": official_score,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        })

        # Notification is blocked when the call is not suitable for official QA.
        delivery_allowed = (
            eligibility["status"] == "gradable"
            and official_score is not None
        )

        marker = {
            "call_id": persisted["id"],
            "call_key": call_key,
            "recipient": agent.get("coaching_email"),
            "email_delivery_allowed": delivery_allowed,
            "email_block_reason": (
                None
                if delivery_allowed
                else eligibility.get("reason") or "Call is not gradable."
            ),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # Publishing the marker LAST is intentional. Notification can only start
        # after the durable call row exists.
        storage_client.bucket(COMPLETION_BUCKET).blob(
            f"ready/{call_key}.json"
        ).upload_from_string(
            json.dumps(marker),
            content_type="application/json",
        )

        repository.mark_lock_complete(
            lock_name=lock_name,
            call_id=persisted["id"],
        )

        return {
            "status": "complete",
            "call_id": persisted["id"],
            "score": official_score,
        }

    except Exception:
        # Production code distinguishes retryable, terminal, and integrity
        # failures. This public example simply releases the lock and re-raises.
        repository.release_processing_lock(lock_name)
        raise
