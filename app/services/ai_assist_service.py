# app/services/ai_assist_service.py
"""AI-assist provider client, prompts, rate limit, and in-memory call log.

Design notes:
- New file only — reads META_* env vars directly so no config-file change is
  needed. Documented in `.env.example`.
- Human always edits and submits; this service only drafts text.
- User/listing text is untrusted and travels inside delimited blocks.
- Seeker contact details never leave the server (enforced at the call site by
  only passing non-PII fields into the prompt builders).
- Fail closed with fixed messages; never `str(e)` (PREFLIGHT Rule 15).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

PROVIDER_CHAT_PATH = "/chat/completions"  # pragma: allowlist secret
LOG_BUFFER_SIZE = 100

AI_ASSIST_UNAVAILABLE_DETAIL = "AI assist service is unavailable"
AI_ASSIST_PROVIDER_FAILED_DETAIL = "AI assist provider request failed"
AI_ASSIST_RATE_LIMITED_DETAIL = "AI assist rate limit exceeded. Please try again later."
AI_ASSIST_FORBIDDEN_DETAIL = "Not enough permissions for AI assist"


def get_base_url() -> str:
    """Provider base URL. Default matches the confirmed provider."""
    return os.getenv("META_MODEL_BASE_URL", "https://api.meta.ai/v1").rstrip("/")


def get_model_id() -> str:
    """Provider model ID. Default matches the confirmed model."""
    return os.getenv("META_MODEL_ID", "muse-spark-1.3")


def get_api_key() -> str:
    """Provider API key. Empty means unconfigured — fail closed with 503."""
    return os.getenv("META_MODEL_API_KEY", "")


def is_demo_enabled() -> bool:
    """Demo flag gating GET /logs. Admin-only plus this flag."""
    return os.getenv("AI_ASSIST_DEMO_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def get_timeout_seconds() -> float:
    """Provider HTTP timeout in seconds."""
    try:
        return float(os.getenv("AI_ASSIST_TIMEOUT_SECONDS", "15"))
    except ValueError:
        return 15.0


def get_max_tokens() -> int:
    """Max-tokens cap sent to the provider."""
    try:
        return int(os.getenv("AI_ASSIST_MAX_TOKENS", "300"))
    except ValueError:
        return 300


def get_rate_limit_max() -> int:
    """Per-user max requests per window."""
    try:
        return int(os.getenv("AI_ASSIST_RATE_LIMIT_MAX", "10"))
    except ValueError:
        return 10


def get_rate_limit_window_seconds() -> float:
    """Per-user rate-limit window in seconds."""
    try:
        return float(os.getenv("AI_ASSIST_RATE_LIMIT_WINDOW_SECONDS", "60"))  # pragma: allowlist secret
    except ValueError:
        return 60.0


def get_endpoint_host() -> str:
    """Host portion of the provider base URL (safe for logs/meta)."""
    base_url = get_base_url()
    try:
        host = urlsplit(base_url).hostname
    except ValueError:
        host = None
    return host or base_url


# --- Per-user in-memory rate limit ---

_RATE_BUCKETS: dict[int, list[float]] = {}
_RATE_LOCK = threading.Lock()


def ensure_ai_assist_rate_limit(user_id: int) -> None:
    """Raise 429 when the per-user window is exhausted. Fail closed."""
    max_requests = get_rate_limit_max()
    window = get_rate_limit_window_seconds()
    now = time.monotonic()
    with _RATE_LOCK:
        bucket = _RATE_BUCKETS.get(user_id, [])
        bucket = [stamp for stamp in bucket if now - stamp < window]
        if len(bucket) >= max_requests:
            _RATE_BUCKETS[user_id] = bucket
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=AI_ASSIST_RATE_LIMITED_DETAIL,
            )
        bucket.append(now)
        _RATE_BUCKETS[user_id] = bucket


def reset_ai_assist_rate_limit() -> None:
    """Clear all per-user buckets. Tests only."""
    with _RATE_LOCK:
        _RATE_BUCKETS.clear()


# --- In-memory ring buffer for call logs (no migration) ---

_LOGS: deque[dict[str, Any]] = deque(maxlen=LOG_BUFFER_SIZE)
_LOGS_LOCK = threading.Lock()


def record_ai_assist_log(entry: dict[str, Any]) -> None:
    """Append one call record. Caller must exclude user text and PII."""
    with _LOGS_LOCK:
        _LOGS.append(entry)


def get_ai_assist_logs(limit: int) -> list[dict[str, Any]]:
    """Return the newest-first slice of the ring buffer."""
    safe_limit = max(1, min(limit, LOG_BUFFER_SIZE))
    with _LOGS_LOCK:
        items = list(_LOGS)
    items.reverse()
    return items[:safe_limit]


def clear_ai_assist_logs() -> None:
    """Empty the ring buffer. Tests only."""
    with _LOGS_LOCK:
        _LOGS.clear()


# --- Prompt builders (untrusted input in delimited blocks) ---

MODERATION_SYSTEM_PROMPT = (
    "You draft short, professional moderation reasons for a human reviewer. "
    "A human always edits and submits. Keep the draft under 80 words. "
    "Never invent policy. Output the draft only. "
    "Do not mention internal identifiers, status names, or field names. "
    "If the listing gives no basis for the action, say so plainly instead of drafting a reason. "
    "Never advise the reviewer to take a different action."
)

INQUIRY_SYSTEM_PROMPT = (
    "You draft a short, polite property-inquiry reply for an agent. "
    "A human always edits and sends. Never include contact details. "
    "Output the draft only."
)


MODERATION_ACTION_LABELS = {
    "agency_reject": "agency rejection",
    "admin_reject": "administrator rejection",
    "revoke": "revocation of a live listing",
}


def _humanize_code(value: str) -> str:
    """Turn an internal snake_case code into plain words."""
    return " ".join(value.split("_")).strip()


def build_moderation_messages(
    *,
    action_type: str,
    listing_title: str,
    listing_description: str,
    moderation_status: str,
    listing_type: str = "",
    location_name: str = "",
    price_text: str = "",
) -> list[dict[str, str]]:
    """Build chat messages for a moderation-reason draft.

    Enum codes are converted to plain wording first, so internal identifiers
    never reach the model verbatim.
    """
    action_label = MODERATION_ACTION_LABELS.get(action_type, _humanize_code(action_type))
    status_label = _humanize_code(moderation_status)
    context_lines = [
        f"Action: {action_label}",
        f"Listing status: {status_label}",
    ]
    if listing_type:
        context_lines.append(f"Listing type: {listing_type}")
    if location_name:
        context_lines.append(f"Location: {location_name}")
    if price_text:
        context_lines.append(f"Price: {price_text}")
    user_block = (
        "\n".join(context_lines) + "\n"
        "<<<UNTRUSTED_LISTING_TITLE\n"
        f"{listing_title}\n"
        "END_UNTRUSTED_LISTING_TITLE>>>\n"
        "<<<UNTRUSTED_LISTING_DESCRIPTION\n"
        f"{listing_description}\n"
        "END_UNTRUSTED_LISTING_DESCRIPTION>>>\n"
        f"Draft a moderation reason for {action_label}. "
        "A human will edit and submit."
    )
    return [
        {"role": "system", "content": MODERATION_SYSTEM_PROMPT},
        {"role": "user", "content": user_block},
    ]


def build_inquiry_messages(
    *,
    property_title: str,
    location_name: str,
    price_text: str,
    inquiry_message: str,
) -> list[dict[str, str]]:
    """Build chat messages for an inquiry-reply draft.

    Callers pass only non-PII context. Seeker name/email/phone must never
    reach this builder.
    """
    user_block = (
        f"Property: {property_title}\n"
        f"Location: {location_name}\n"
        f"Price: {price_text}\n"
        "<<<UNTRUSTED_INQUIRY_MESSAGE\n"
        f"{inquiry_message}\n"
        "END_UNTRUSTED_INQUIRY_MESSAGE>>>\n"
        "Draft a helpful reply to the inquiry above. "
        "A human will edit and send."
    )
    return [
        {"role": "system", "content": INQUIRY_SYSTEM_PROMPT},
        {"role": "user", "content": user_block},
    ]


# --- Provider call ---


def _extract_provider_request_id(
    payload: dict[str, Any], headers: Any
) -> str:
    """Provider request ID from body `id`, else response header, else unknown."""
    raw_id = payload.get("id")
    if isinstance(raw_id, str) and raw_id.strip():
        return raw_id.strip()
    try:
        header_value = headers.get("x-request-id") or headers.get("x-requestid")
    except Exception:
        header_value = None
    if isinstance(header_value, str) and header_value.strip():
        return header_value.strip()
    return "unknown"


def _extract_draft(payload: dict[str, Any]) -> str:
    """Draft text from an OpenAI-compatible choices payload."""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    text = first.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    return ""


def fetch_ai_draft(messages: list[dict[str, str]]) -> tuple[str, str]:
    """Call the provider and return (draft, provider_request_id).

    Raises:
        HTTPException 503 when the API key is missing.
        HTTPException 502 on any provider/transport/payload failure.
    """
    api_key = get_api_key()
    if not api_key:
        logger.warning("ai_assist_unconfigured")  # pragma: allowlist secret
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=AI_ASSIST_UNAVAILABLE_DETAIL,
        )

    url = f"{get_base_url()}{PROVIDER_CHAT_PATH}"
    body = {
        "model": get_model_id(),
        "messages": messages,
        "max_tokens": get_max_tokens(),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = httpx.post(url, json=body, headers=headers, timeout=get_timeout_seconds())
    except Exception as exc:
        logger.warning(
            "ai_assist_provider_transport_failed",  # pragma: allowlist secret
            extra={"error": exc.__class__.__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=AI_ASSIST_PROVIDER_FAILED_DETAIL,
        )

    if response.status_code < 200 or response.status_code >= 300:
        logger.warning(
            "ai_assist_provider_status_failed",  # pragma: allowlist secret
            extra={"provider_status": response.status_code},  # pragma: allowlist secret
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=AI_ASSIST_PROVIDER_FAILED_DETAIL,
        )

    try:
        payload = response.json()
    except Exception as exc:
        logger.warning(
            "ai_assist_provider_body_failed",  # pragma: allowlist secret
            extra={"error": exc.__class__.__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=AI_ASSIST_PROVIDER_FAILED_DETAIL,
        )

    if not isinstance(payload, dict):
        logger.warning("ai_assist_provider_shape_failed")  # pragma: allowlist secret
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=AI_ASSIST_PROVIDER_FAILED_DETAIL,
        )

    draft = _extract_draft(payload)
    if not draft:
        logger.warning("ai_assist_provider_empty_draft")  # pragma: allowlist secret
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=AI_ASSIST_PROVIDER_FAILED_DETAIL,
        )

    provider_request_id = _extract_provider_request_id(payload, response.headers)
    return draft, provider_request_id
