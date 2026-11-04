# app/schemas/ai_assist.py
"""Pydantic schemas for AI-assist draft endpoints.

A human always edits and submits. Moderation reasons remain required (T-001).
These schemas carry IDs only — the server loads listing/inquiry rows.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict


class AiAssistActionType(str, Enum):
    """Moderation draft action — mirrors the transition-guard targets."""

    agency_reject = "agency_reject"
    admin_reject = "admin_reject"
    revoke = "revoke"


class ModerationReasonRequest(BaseModel):
    """Request a draft moderation reason for a listing."""

    listing_id: int
    action_type: AiAssistActionType

    model_config = ConfigDict(use_enum_values=False)


class InquiryReplyAssistRequest(BaseModel):
    """Request a draft reply for an inquiry."""

    inquiry_id: int


class AiAssistMeta(BaseModel):
    """Provider + local correlation metadata for a draft call."""

    model_id: str
    endpoint_host: str
    provider_request_id: str
    local_request_id: str
    latency_ms: int

    model_config = ConfigDict(from_attributes=True)


class AiAssistResponse(BaseModel):
    """Draft text plus correlation metadata."""

    draft: str
    meta: AiAssistMeta

    model_config = ConfigDict(from_attributes=True)


class AiAssistLogEntry(BaseModel):
    """One in-memory ring-buffer record. No user text, no contact details."""

    timestamp: str
    user_id: int
    kind: str
    listing_id: Optional[int] = None
    inquiry_id: Optional[int] = None
    action_type: Optional[str] = None
    local_request_id: str
    provider_request_id: Optional[str] = None
    model_id: str
    endpoint_host: str
    latency_ms: int
    success: bool

    model_config = ConfigDict(from_attributes=True)
