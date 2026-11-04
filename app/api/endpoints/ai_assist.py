# app/api/endpoints/ai_assist.py
"""AI-assist draft endpoints — humans edit and submit, moderation stays required.

- POST /moderation-reason drafts agency_reject / admin_reject / revoke reasons.
- POST /inquiry-reply drafts an agent reply without seeker PII leaving the server.
- GET /logs serves the in-memory ring buffer (admin + demo flag only).
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, List, cast as typing_cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_current_active_user, validate_request_size
from app.crud.inquiries import inquiry as inquiry_crud
from app.crud.properties import property as property_crud
from app.crud.users import user as user_crud
from app.models.users import User
from app.schemas.ai_assist import (
    AiAssistActionType,
    AiAssistLogEntry,
    AiAssistMeta,
    AiAssistResponse,
    InquiryReplyAssistRequest,
    ModerationReasonRequest,
)
from app.schemas.users import UserResponse
from app.services.ai_assist_service import (
    AI_ASSIST_FORBIDDEN_DETAIL,
    build_inquiry_messages,
    build_moderation_messages,
    ensure_ai_assist_rate_limit,
    fetch_ai_draft,
    get_ai_assist_logs,
    get_endpoint_host,
    get_model_id,
    is_demo_enabled,
    record_ai_assist_log,
)

router = APIRouter()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_listing_moderation_authority(
    *,
    action_type: AiAssistActionType,
    property_agency_id: int | None,
    current_user: UserResponse,
) -> None:
    """Mirror the transition-guard role authority for each draft action.

    - agency_reject: listing's agency owner only (agency_id must match).
    - admin_reject / revoke: admin only.
    Status legality (from-state) is intentionally not enforced — a draft is
    assistive text, not the transition itself.
    """
    current_model = typing_cast(User, current_user)
    if action_type == AiAssistActionType.agency_reject:
        user_agency_id = typing_cast(int | None, current_user.agency_id)
        if not user_crud.is_agency_owner(current_model):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=AI_ASSIST_FORBIDDEN_DETAIL,
            )
        if property_agency_id is None or user_agency_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=AI_ASSIST_FORBIDDEN_DETAIL,
            )
        if property_agency_id != user_agency_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=AI_ASSIST_FORBIDDEN_DETAIL,
            )
        return

    if not user_crud.is_admin(current_model):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=AI_ASSIST_FORBIDDEN_DETAIL,
        )


def _require_inquiry_reply_authority(
    *,
    property_owner_id: int | None,
    property_agency_id: int | None,
    current_user: UserResponse,
) -> None:
    """Allow the receiving agent (property owner) or the agency owner.

    Admins are not included by design — this drafts an agent voice reply.
    """
    current_user_id = typing_cast(int, current_user.user_id)
    current_model = typing_cast(User, current_user)
    if property_owner_id is not None and property_owner_id == current_user_id:
        return
    if user_crud.is_agency_owner(current_model):
        user_agency_id = typing_cast(int | None, current_user.agency_id)
        if (
            property_agency_id is not None
            and user_agency_id is not None
            and property_agency_id == user_agency_id
        ):
            return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=AI_ASSIST_FORBIDDEN_DETAIL,
    )


@router.post("/moderation-reason", response_model=AiAssistResponse)
def suggest_moderation_reason(
    *,
    db: Session = Depends(get_db),
    body: ModerationReasonRequest,
    current_user: UserResponse = Depends(get_current_active_user),
    _: None = Depends(validate_request_size),
) -> Any:
    """Draft a moderation reason for a listing. Human edits and submits."""
    ensure_ai_assist_rate_limit(typing_cast(int, current_user.user_id))

    listing = property_crud.get(db=db, property_id=body.listing_id)
    if listing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found",
        )

    listing_agency_id = typing_cast(int | None, listing.agency_id)
    _require_listing_moderation_authority(
        action_type=body.action_type,
        property_agency_id=listing_agency_id,
        current_user=current_user,
    )

    listing_title = str(getattr(listing, "title", "") or "")
    listing_description = str(getattr(listing, "description", "") or "")
    raw_status = getattr(listing, "moderation_status", "draft")
    moderation_status = str(getattr(raw_status, "value", raw_status))

    messages = build_moderation_messages(
        action_type=body.action_type.value,
        listing_title=listing_title,
        listing_description=listing_description,
        moderation_status=moderation_status,
    )

    local_request_id = uuid.uuid4().hex  # pragma: allowlist secret
    started = time.monotonic()
    try:
        draft, provider_request_id = fetch_ai_draft(messages)
    except HTTPException as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        if exc.status_code in (
            status.HTTP_502_BAD_GATEWAY,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ):
            record_ai_assist_log(
                {
                    "timestamp": _utc_now_iso(),
                    "user_id": typing_cast(int, current_user.user_id),
                    "kind": "moderation-reason",
                    "listing_id": body.listing_id,
                    "inquiry_id": None,
                    "action_type": body.action_type.value,
                    "local_request_id": local_request_id,  # pragma: allowlist secret
                    "provider_request_id": None,
                    "model_id": get_model_id(),
                    "endpoint_host": get_endpoint_host(),
                    "latency_ms": latency_ms,
                    "success": False,
                }
            )
        raise
    latency_ms = int((time.monotonic() - started) * 1000)

    record_ai_assist_log(
        {
            "timestamp": _utc_now_iso(),
            "user_id": typing_cast(int, current_user.user_id),
            "kind": "moderation-reason",
            "listing_id": body.listing_id,
            "inquiry_id": None,
            "action_type": body.action_type.value,
            "local_request_id": local_request_id,  # pragma: allowlist secret
            "provider_request_id": provider_request_id,
            "model_id": get_model_id(),
            "endpoint_host": get_endpoint_host(),
            "latency_ms": latency_ms,
            "success": True,
        }
    )

    return {
        "draft": draft,
        "meta": {
            "model_id": get_model_id(),
            "endpoint_host": get_endpoint_host(),
            "provider_request_id": provider_request_id,
            "local_request_id": local_request_id,  # pragma: allowlist secret
            "latency_ms": latency_ms,
        },
    }


@router.post("/inquiry-reply", response_model=AiAssistResponse)
def suggest_inquiry_reply(
    *,
    db: Session = Depends(get_db),
    body: InquiryReplyAssistRequest,
    current_user: UserResponse = Depends(get_current_active_user),
    _: None = Depends(validate_request_size),
) -> Any:
    """Draft an inquiry reply. Seeker contact details never leave the server."""
    ensure_ai_assist_rate_limit(typing_cast(int, current_user.user_id))

    inquiry = inquiry_crud.get(db=db, inquiry_id=body.inquiry_id)
    if inquiry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inquiry not found",
        )

    inquiry_property_id = typing_cast(int, inquiry.property_id)
    listing = property_crud.get(db=db, property_id=inquiry_property_id)
    if listing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated property not found",
        )

    _require_inquiry_reply_authority(
        property_owner_id=typing_cast(int | None, listing.user_id),
        property_agency_id=typing_cast(int | None, listing.agency_id),
        current_user=current_user,
    )

    # Non-PII context only — never pass seeker name/email/phone to the builder.
    property_title = str(getattr(listing, "title", "") or "")
    location_name = str(getattr(listing, "location_name", "") or "") or "Lagos, Nigeria"
    price_value = getattr(listing, "price", None)
    currency = str(getattr(listing, "price_currency", "") or "NGN")
    price_text = f"{price_value} {currency}".strip() if price_value is not None else currency
    inquiry_message = str(getattr(inquiry, "message", "") or "")

    messages = build_inquiry_messages(
        property_title=property_title,
        location_name=location_name,
        price_text=price_text,
        inquiry_message=inquiry_message,
    )

    local_request_id = uuid.uuid4().hex  # pragma: allowlist secret
    started = time.monotonic()
    try:
        draft, provider_request_id = fetch_ai_draft(messages)
    except HTTPException as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        if exc.status_code in (
            status.HTTP_502_BAD_GATEWAY,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ):
            record_ai_assist_log(
                {
                    "timestamp": _utc_now_iso(),
                    "user_id": typing_cast(int, current_user.user_id),
                    "kind": "inquiry-reply",
                    "listing_id": None,
                    "inquiry_id": body.inquiry_id,
                    "action_type": None,
                    "local_request_id": local_request_id,  # pragma: allowlist secret
                    "provider_request_id": None,
                    "model_id": get_model_id(),
                    "endpoint_host": get_endpoint_host(),
                    "latency_ms": latency_ms,
                    "success": False,
                }
            )
        raise
    latency_ms = int((time.monotonic() - started) * 1000)

    record_ai_assist_log(
        {
            "timestamp": _utc_now_iso(),
            "user_id": typing_cast(int, current_user.user_id),
            "kind": "inquiry-reply",
            "listing_id": None,
            "inquiry_id": body.inquiry_id,
            "action_type": None,
            "local_request_id": local_request_id,  # pragma: allowlist secret
            "provider_request_id": provider_request_id,
            "model_id": get_model_id(),
            "endpoint_host": get_endpoint_host(),
            "latency_ms": latency_ms,
            "success": True,
        }
    )

    return {
        "draft": draft,
        "meta": {
            "model_id": get_model_id(),
            "endpoint_host": get_endpoint_host(),
            "provider_request_id": provider_request_id,
            "local_request_id": local_request_id,  # pragma: allowlist secret
            "latency_ms": latency_ms,
        },
    }


@router.get("/logs", response_model=List[AiAssistLogEntry])
def read_ai_assist_logs(
    *,
    limit: int = Query(default=20, ge=1, le=100),
    current_user: UserResponse = Depends(get_current_active_user),
) -> Any:
    """Return the last N AI-assist calls. Admin + demo flag only."""
    current_model = typing_cast(User, current_user)
    if not user_crud.is_admin(current_model):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=AI_ASSIST_FORBIDDEN_DETAIL,
        )
    if not is_demo_enabled():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=AI_ASSIST_FORBIDDEN_DETAIL,
        )
    return get_ai_assist_logs(limit)
