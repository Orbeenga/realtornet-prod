# tests/api/endpoints/test_ai_assist.py
"""AI-assist endpoint + service tests with a mocked HTTP client.

Provider: https://api.meta.ai/v1 / muse-spark-1.3 (env-driven, mocked here).
No real network calls. No `.env` values read — env is set per test.
"""

from __future__ import annotations

import json as json_module
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.crud.properties import property as property_crud
from app.models.inquiries import Inquiry
from app.models.properties import ModerationStatus
from app.schemas.properties import PropertyCreate
from app.services import ai_assist_service as service


@pytest.fixture(autouse=True)
def _ai_assist_isolation(monkeypatch: pytest.MonkeyPatch):
    """Fresh rate buckets + logs per test; provider key present by default."""
    service.reset_ai_assist_rate_limit()
    service.clear_ai_assist_logs()
    monkeypatch.setenv("META_MODEL_API_KEY", "test-key")
    monkeypatch.setenv("META_MODEL_BASE_URL", "https://api.meta.ai/v1")
    monkeypatch.setenv("META_MODEL_ID", "muse-spark-1.3")
    monkeypatch.delenv("AI_ASSIST_DEMO_ENABLED", raising=False)
    yield
    service.reset_ai_assist_rate_limit()
    service.clear_ai_assist_logs()


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: Any = None,
        headers: dict[str, str] | None = None,
        raw_text: str | None = None,
        json_error: bool = False,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self._raw_text = raw_text
        self._json_error = json_error

    def json(self) -> Any:
        if self._json_error:
            raise ValueError("bad json")
        if self._raw_text is not None:
            return json_module.loads(self._raw_text)
        return self._payload


def _ok_payload(*, request_id: str = "prov-123", draft: str = "Draft reason text") -> dict[str, Any]:
    return {
        "id": request_id,
        "choices": [{"message": {"content": draft}}],
    }


def _patch_post(monkeypatch: pytest.MonkeyPatch, fake: _FakeResponse) -> dict[str, Any]:
    """Patch httpx.post; capture call kwargs for payload assertions."""
    captured: dict[str, Any] = {}

    def _fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured.update(kwargs)
        return fake

    monkeypatch.setattr(service.httpx, "post", _fake_post)
    return captured


def _make_agency_listing(db: Session, *, owner, agency, location, property_type, title: str = "Lagos Flat"):
    obj_in = PropertyCreate(
        title=title,
        description="Two-bed flat near the market",
        price=5000000,
        bedrooms=2,
        bathrooms=1.0,
        location_id=location.location_id,
        property_type_id=property_type.property_type_id,
        listing_type="sale",
        listing_status="available",
        agency_id=agency.agency_id,
    )
    prop = property_crud.create_with_owner(db, obj_in=obj_in, user_id=owner.user_id)
    db.flush()
    db.refresh(prop)
    return prop


def _make_inquiry(db: Session, *, seeker, listing, message: str = "Is this still available?") -> Inquiry:
    row = Inquiry(user_id=seeker.user_id, property_id=listing.property_id, message=message)
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


class TestModerationReason:
    def test_unauthenticated_returns_401(self, client: TestClient):
        response = client.post(
            "/api/v1/ai-assist/moderation-reason",  # pragma: allowlist secret
            json={"listing_id": 1, "action_type": "agency_reject"},
        )
        assert response.status_code == 401

    def test_listing_not_found_returns_404(
        self, client: TestClient, agency_owner_token_headers, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_post(monkeypatch, _FakeResponse(payload=_ok_payload()))
        response = client.post(
            "/api/v1/ai-assist/moderation-reason",  # pragma: allowlist secret
            json={"listing_id": 999999, "action_type": "agency_reject"},
            headers=agency_owner_token_headers,
        )
        assert response.status_code == 404

    def test_agency_reject_success(
        self,
        client: TestClient,
        db: Session,
        agency_owner_token_headers,
        agency_owner_user,
        agent_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        captured = _patch_post(monkeypatch, _FakeResponse(payload=_ok_payload()))
        response = client.post(
            "/api/v1/ai-assist/moderation-reason",  # pragma: allowlist secret
            json={"listing_id": listing.property_id, "action_type": "agency_reject"},
            headers=agency_owner_token_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["draft"] == "Draft reason text"
        assert data["meta"]["model_id"] == "muse-spark-1.3"
        assert data["meta"]["endpoint_host"] == "api.meta.ai"
        assert data["meta"]["provider_request_id"] == "prov-123"
        assert data["meta"]["local_request_id"]  # pragma: allowlist secret
        assert isinstance(data["meta"]["latency_ms"], int)
        # Provider payload: delimited untrusted blocks + caps.
        sent = captured["json"]
        assert sent["model"] == "muse-spark-1.3"
        assert sent["max_tokens"] == 300
        user_text = sent["messages"][1]["content"]
        assert "<<<UNTRUSTED_LISTING_TITLE" in user_text
        assert "<<<UNTRUSTED_LISTING_DESCRIPTION" in user_text
        assert captured["url"] == "https://api.meta.ai/v1/chat/completions"
        assert captured["timeout"] == 15.0

    def test_agency_reject_forbidden_for_agent(
        self,
        client: TestClient,
        db: Session,
        agent_token_headers,
        agent_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        _patch_post(monkeypatch, _FakeResponse(payload=_ok_payload()))
        response = client.post(
            "/api/v1/ai-assist/moderation-reason",  # pragma: allowlist secret
            json={"listing_id": listing.property_id, "action_type": "agency_reject"},
            headers=agent_token_headers,
        )
        assert response.status_code == 403

    def test_agency_reject_forbidden_for_admin(
        self,
        client: TestClient,
        db: Session,
        admin_token_headers,
        agent_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        _patch_post(monkeypatch, _FakeResponse(payload=_ok_payload()))
        response = client.post(
            "/api/v1/ai-assist/moderation-reason",  # pragma: allowlist secret
            json={"listing_id": listing.property_id, "action_type": "agency_reject"},
            headers=admin_token_headers,
        )
        assert response.status_code == 403

    def test_agency_reject_forbidden_for_other_agency_owner(
        self,
        client: TestClient,
        db: Session,
        agent_user,
        agency,
        other_agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from app.core.security import generate_access_token, get_password_hash
        from app.models.users import User, UserRole
        import uuid

        other_owner = User(
            email="other_owner@example.com",
            password_hash=get_password_hash("password"),
            first_name="Other",
            last_name="Owner",
            user_role=UserRole.AGENCY_OWNER,
            supabase_id=uuid.uuid4(),
            agency_id=other_agency.agency_id,
        )
        db.add(other_owner)
        db.flush()
        db.refresh(other_owner)
        token = generate_access_token(
            supabase_id=other_owner.supabase_id,
            user_id=other_owner.user_id,
            user_role=other_owner.user_role.value,
            agency_id=other_agency.agency_id,
        )
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        _patch_post(monkeypatch, _FakeResponse(payload=_ok_payload()))
        response = client.post(
            "/api/v1/ai-assist/moderation-reason",  # pragma: allowlist secret
            json={"listing_id": listing.property_id, "action_type": "agency_reject"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    def test_admin_reject_success_for_admin(
        self,
        client: TestClient,
        db: Session,
        admin_token_headers,
        agent_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        listing.moderation_status = ModerationStatus.admin_review
        db.flush()
        _patch_post(monkeypatch, _FakeResponse(payload=_ok_payload(draft="Admin draft")))
        response = client.post(
            "/api/v1/ai-assist/moderation-reason",  # pragma: allowlist secret
            json={"listing_id": listing.property_id, "action_type": "admin_reject"},
            headers=admin_token_headers,
        )
        assert response.status_code == 200
        assert response.json()["draft"] == "Admin draft"

    def test_admin_reject_forbidden_for_agency_owner(
        self,
        client: TestClient,
        db: Session,
        agency_owner_token_headers,
        agent_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        _patch_post(monkeypatch, _FakeResponse(payload=_ok_payload()))
        response = client.post(
            "/api/v1/ai-assist/moderation-reason",  # pragma: allowlist secret
            json={"listing_id": listing.property_id, "action_type": "admin_reject"},
            headers=agency_owner_token_headers,
        )
        assert response.status_code == 403

    def test_revoke_success_for_admin(
        self,
        client: TestClient,
        db: Session,
        admin_token_headers,
        agent_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        listing.moderation_status = ModerationStatus.live
        db.flush()
        _patch_post(monkeypatch, _FakeResponse(payload=_ok_payload(draft="Revoke draft")))
        response = client.post(
            "/api/v1/ai-assist/moderation-reason",  # pragma: allowlist secret
            json={"listing_id": listing.property_id, "action_type": "revoke"},
            headers=admin_token_headers,
        )
        assert response.status_code == 200

    def test_missing_key_returns_503_fixed_message(
        self,
        client: TestClient,
        db: Session,
        agency_owner_token_headers,
        agent_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        monkeypatch.setenv("META_MODEL_API_KEY", "")
        response = client.post(
            "/api/v1/ai-assist/moderation-reason",  # pragma: allowlist secret
            json={"listing_id": listing.property_id, "action_type": "agency_reject"},
            headers=agency_owner_token_headers,
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "AI assist service is unavailable"

    def test_provider_status_error_returns_502_fixed(
        self,
        client: TestClient,
        db: Session,
        admin_token_headers,
        agent_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        _patch_post(monkeypatch, _FakeResponse(status_code=500, payload={"error": "boom"}))
        response = client.post(
            "/api/v1/ai-assist/moderation-reason",  # pragma: allowlist secret
            json={"listing_id": listing.property_id, "action_type": "revoke"},
            headers=admin_token_headers,
        )
        assert response.status_code == 502
        assert response.json()["detail"] == "AI assist provider request failed"

    def test_provider_transport_error_returns_502(
        self,
        client: TestClient,
        db: Session,
        admin_token_headers,
        agent_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )

        def _boom(url: str, **kwargs: Any):
            raise service.httpx.TimeoutException("slow")

        monkeypatch.setattr(service.httpx, "post", _boom)
        response = client.post(
            "/api/v1/ai-assist/moderation-reason",  # pragma: allowlist secret
            json={"listing_id": listing.property_id, "action_type": "revoke"},
            headers=admin_token_headers,
        )
        assert response.status_code == 502

    def test_provider_invalid_json_returns_502(
        self,
        client: TestClient,
        db: Session,
        admin_token_headers,
        agent_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        _patch_post(monkeypatch, _FakeResponse(payload=None, json_error=True))
        response = client.post(
            "/api/v1/ai-assist/moderation-reason",  # pragma: allowlist secret
            json={"listing_id": listing.property_id, "action_type": "revoke"},
            headers=admin_token_headers,
        )
        assert response.status_code == 502

    def test_provider_non_dict_payload_returns_502(
        self,
        client: TestClient,
        db: Session,
        admin_token_headers,
        agent_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        _patch_post(monkeypatch, _FakeResponse(payload=["not", "a", "dict"]))
        response = client.post(
            "/api/v1/ai-assist/moderation-reason",  # pragma: allowlist secret
            json={"listing_id": listing.property_id, "action_type": "revoke"},
            headers=admin_token_headers,
        )
        assert response.status_code == 502

    def test_provider_empty_choices_returns_502(
        self,
        client: TestClient,
        db: Session,
        admin_token_headers,
        agent_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        _patch_post(monkeypatch, _FakeResponse(payload={"id": "x", "choices": []}))
        response = client.post(
            "/api/v1/ai-assist/moderation-reason",  # pragma: allowlist secret
            json={"listing_id": listing.property_id, "action_type": "revoke"},
            headers=admin_token_headers,
        )
        assert response.status_code == 502

    def test_rate_limit_returns_429(
        self,
        client: TestClient,
        db: Session,
        admin_token_headers,
        agent_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        _patch_post(monkeypatch, _FakeResponse(payload=_ok_payload()))
        monkeypatch.setenv("AI_ASSIST_RATE_LIMIT_MAX", "2")
        monkeypatch.setenv("AI_ASSIST_RATE_LIMIT_WINDOW_SECONDS", "60")  # pragma: allowlist secret
        for _ in range(2):
            response = client.post(
                "/api/v1/ai-assist/moderation-reason",  # pragma: allowlist secret
                json={"listing_id": listing.property_id, "action_type": "revoke"},
                headers=admin_token_headers,
            )
            assert response.status_code == 200
        limited = client.post(
            "/api/v1/ai-assist/moderation-reason",  # pragma: allowlist secret
            json={"listing_id": listing.property_id, "action_type": "revoke"},
            headers=admin_token_headers,
        )
        assert limited.status_code == 429


class TestInquiryReply:
    def test_unauthenticated_returns_401(self, client: TestClient):
        response = client.post("/api/v1/ai-assist/inquiry-reply", json={"inquiry_id": 1})  # pragma: allowlist secret
        assert response.status_code == 401

    def test_inquiry_not_found_returns_404(
        self, client: TestClient, agent_token_headers, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_post(monkeypatch, _FakeResponse(payload=_ok_payload()))
        response = client.post(
            "/api/v1/ai-assist/inquiry-reply",  # pragma: allowlist secret
            json={"inquiry_id": 999999},
            headers=agent_token_headers,
        )
        assert response.status_code == 404

    def test_success_for_receiving_agent_excludes_seeker_pii(
        self,
        client: TestClient,
        db: Session,
        agent_token_headers,
        agent_user,
        normal_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        inquiry = _make_inquiry(db, seeker=normal_user, listing=listing)
        captured = _patch_post(
            monkeypatch, _FakeResponse(payload=_ok_payload(draft="Thanks for asking"))
        )
        response = client.post(
            "/api/v1/ai-assist/inquiry-reply",  # pragma: allowlist secret
            json={"inquiry_id": inquiry.inquiry_id},
            headers=agent_token_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["draft"] == "Thanks for asking"
        assert data["meta"]["provider_request_id"] == "prov-123"
        sent_text = json_module.dumps(captured["json"])
        assert normal_user.email not in sent_text
        assert (normal_user.phone_number or "+1234567890") not in sent_text
        assert "Test User" not in sent_text
        assert "<<<UNTRUSTED_INQUIRY_MESSAGE" in sent_text

    def test_success_for_agency_owner(
        self,
        client: TestClient,
        db: Session,
        agency_owner_token_headers,
        agent_user,
        normal_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        inquiry = _make_inquiry(db, seeker=normal_user, listing=listing)
        _patch_post(monkeypatch, _FakeResponse(payload=_ok_payload(draft="Owner draft")))
        response = client.post(
            "/api/v1/ai-assist/inquiry-reply",  # pragma: allowlist secret
            json={"inquiry_id": inquiry.inquiry_id},
            headers=agency_owner_token_headers,
        )
        assert response.status_code == 200

    def test_seeker_cannot_draft_reply(
        self,
        client: TestClient,
        db: Session,
        normal_user_token_headers,
        agent_user,
        normal_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        inquiry = _make_inquiry(db, seeker=normal_user, listing=listing)
        _patch_post(monkeypatch, _FakeResponse(payload=_ok_payload()))
        response = client.post(
            "/api/v1/ai-assist/inquiry-reply",  # pragma: allowlist secret
            json={"inquiry_id": inquiry.inquiry_id},
            headers=normal_user_token_headers,
        )
        assert response.status_code == 403

    def test_admin_cannot_draft_inquiry_reply(
        self,
        client: TestClient,
        db: Session,
        admin_token_headers,
        agent_user,
        normal_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        inquiry = _make_inquiry(db, seeker=normal_user, listing=listing)
        _patch_post(monkeypatch, _FakeResponse(payload=_ok_payload()))
        response = client.post(
            "/api/v1/ai-assist/inquiry-reply",  # pragma: allowlist secret
            json={"inquiry_id": inquiry.inquiry_id},
            headers=admin_token_headers,
        )
        assert response.status_code == 403

    def test_missing_property_returns_404(
        self,
        client: TestClient,
        db: Session,
        agent_token_headers,
        agent_user,
        normal_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        inquiry = _make_inquiry(db, seeker=normal_user, listing=listing)
        db.delete(listing)
        db.flush()
        _patch_post(monkeypatch, _FakeResponse(payload=_ok_payload()))
        response = client.post(
            "/api/v1/ai-assist/inquiry-reply",  # pragma: allowlist secret
            json={"inquiry_id": inquiry.inquiry_id},
            headers=agent_token_headers,
        )
        assert response.status_code in (404, 403)

    def test_provider_failure_returns_502_and_logs(
        self,
        client: TestClient,
        db: Session,
        agent_token_headers,
        agent_user,
        normal_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        inquiry = _make_inquiry(db, seeker=normal_user, listing=listing)
        _patch_post(monkeypatch, _FakeResponse(status_code=502, payload={}))
        response = client.post(
            "/api/v1/ai-assist/inquiry-reply",  # pragma: allowlist secret
            json={"inquiry_id": inquiry.inquiry_id},
            headers=agent_token_headers,
        )
        assert response.status_code == 502
        logs = service.get_ai_assist_logs(10)
        assert len(logs) == 1
        assert logs[0]["success"] is False
        assert logs[0]["kind"] == "inquiry-reply"


class TestLogs:
    def test_unauthenticated_returns_401(self, client: TestClient):
        assert client.get("/api/v1/ai-assist/logs").status_code == 401

    def test_non_admin_forbidden(
        self, client: TestClient, agent_token_headers, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("AI_ASSIST_DEMO_ENABLED", "true")
        response = client.get("/api/v1/ai-assist/logs", headers=agent_token_headers)
        assert response.status_code == 403

    def test_admin_without_demo_flag_forbidden(
        self, client: TestClient, admin_token_headers, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("AI_ASSIST_DEMO_ENABLED", "false")
        response = client.get("/api/v1/ai-assist/logs", headers=admin_token_headers)
        assert response.status_code == 403

    def test_admin_with_demo_flag_returns_newest_first(
        self,
        client: TestClient,
        db: Session,
        admin_token_headers,
        agent_user,
        agency,
        location,
        property_type,
        monkeypatch: pytest.MonkeyPatch,
    ):
        listing = _make_agency_listing(
            db, owner=agent_user, agency=agency, location=location, property_type=property_type
        )
        _patch_post(monkeypatch, _FakeResponse(payload=_ok_payload(request_id="prov-1")))
        first = client.post(
            "/api/v1/ai-assist/moderation-reason",  # pragma: allowlist secret
            json={"listing_id": listing.property_id, "action_type": "revoke"},
            headers=admin_token_headers,
        )
        assert first.status_code == 200
        _patch_post(monkeypatch, _FakeResponse(payload=_ok_payload(request_id="prov-2")))
        second = client.post(
            "/api/v1/ai-assist/moderation-reason",  # pragma: allowlist secret
            json={"listing_id": listing.property_id, "action_type": "revoke"},
            headers=admin_token_headers,
        )
        assert second.status_code == 200
        monkeypatch.setenv("AI_ASSIST_DEMO_ENABLED", "true")
        response = client.get(
            "/api/v1/ai-assist/logs", params={"limit": 1}, headers=admin_token_headers
        )
        assert response.status_code == 200
        logs = response.json()
        assert len(logs) == 1
        assert logs[0]["provider_request_id"] == "prov-2"
        assert logs[0]["model_id"] == "muse-spark-1.3"
        assert logs[0]["endpoint_host"] == "api.meta.ai"
        assert "draft" not in logs[0]
        assert "message" not in logs[0]


class TestServiceHelpers:
    def test_provider_request_id_from_header(self, monkeypatch: pytest.MonkeyPatch):
        captured = _patch_post(
            monkeypatch,
            _FakeResponse(
                payload={"choices": [{"message": {"content": "hi"}}]},
                headers={"x-request-id": "hdr-9"},
            ),
        )
        assert captured is not None
        draft, provider_id = service.fetch_ai_draft([{"role": "user", "content": "x"}])
        assert draft == "hi"
        assert provider_id == "hdr-9"

    def test_provider_request_id_unknown_fallback(self, monkeypatch: pytest.MonkeyPatch):
        _patch_post(
            monkeypatch,
            _FakeResponse(payload={"choices": [{"message": {"content": "hi"}}]}, headers={}),
        )
        _, provider_id = service.fetch_ai_draft([{"role": "user", "content": "x"}])
        assert provider_id == "unknown"

    def test_provider_text_choice_fallback(self, monkeypatch: pytest.MonkeyPatch):
        _patch_post(
            monkeypatch, _FakeResponse(payload={"id": "t1", "choices": [{"text": "alt draft"}]})
        )
        draft, _ = service.fetch_ai_draft([{"role": "user", "content": "x"}])
        assert draft == "alt draft"

    def test_invalid_env_values_use_defaults(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AI_ASSIST_TIMEOUT_SECONDS", "nope")
        monkeypatch.setenv("AI_ASSIST_MAX_TOKENS", "nope")
        monkeypatch.setenv("AI_ASSIST_RATE_LIMIT_MAX", "nope")
        monkeypatch.setenv("AI_ASSIST_RATE_LIMIT_WINDOW_SECONDS", "nope")  # pragma: allowlist secret
        assert service.get_timeout_seconds() == 15.0
        assert service.get_max_tokens() == 300
        assert service.get_rate_limit_max() == 10
        assert service.get_rate_limit_window_seconds() == 60.0

    def test_endpoint_host_fallback(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("META_MODEL_BASE_URL", "not a url :::")
        assert service.get_endpoint_host()

    def test_endpoint_host_invalid_url_value_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("META_MODEL_BASE_URL", "http://[::1")
        assert service.get_endpoint_host() == "http://[::1"

    def test_demo_flag_variants(self, monkeypatch: pytest.MonkeyPatch):
        for value in ("1", "TRUE", "yes", "on"):
            monkeypatch.setenv("AI_ASSIST_DEMO_ENABLED", value)
            assert service.is_demo_enabled() is True
        monkeypatch.setenv("AI_ASSIST_DEMO_ENABLED", "false")
        assert service.is_demo_enabled() is False

    def test_log_limit_clamped(self):
        service.record_ai_assist_log(
            {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "user_id": 1,
                "kind": "moderation-reason",
                "listing_id": 1,
                "inquiry_id": None,
                "action_type": "revoke",
                "local_request_id": "a",  # pragma: allowlist secret
                "provider_request_id": "p",
                "model_id": "muse-spark-1.3",
                "endpoint_host": "api.meta.ai",
                "latency_ms": 5,
                "success": True,
            }
        )
        assert len(service.get_ai_assist_logs(0)) == 1
        assert len(service.get_ai_assist_logs(500)) == 1

    def test_extract_helpers_edge_cases(self):
        assert service._extract_draft({}) == ""
        assert service._extract_draft({"choices": "nope"}) == ""
        assert service._extract_draft({"choices": ["nope"]}) == ""
        assert service._extract_draft({"choices": [{"message": {"content": "  "}}]}) == ""
        assert service._extract_provider_request_id({"id": "  "}, {}) == "unknown"
        assert service._extract_provider_request_id({}, {"x-request-id": "  "}) == "unknown"

        class _BadHeaders:
            def get(self, key: str):
                raise RuntimeError("bad")

        assert service._extract_provider_request_id({}, _BadHeaders()) == "unknown"


class TestModerationPromptContent:
    """Prompt builders must not leak internal enum codes to the model."""

    @pytest.mark.parametrize(
        ("action_type", "label"),
        [
            ("agency_reject", "agency rejection"),
            ("admin_reject", "administrator rejection"),
        ],
    )
    def test_action_code_never_verbatim(self, action_type: str, label: str):
        messages = service.build_moderation_messages(
            action_type=action_type,
            listing_title="Lagos Flat",
            listing_description="Two-bed flat",
            moderation_status="admin_review",
            listing_type="sale",
            location_name="Lagos, Nigeria",
            price_text="5000000 NGN",
        )
        content = "\n".join(m["content"] for m in messages)
        assert action_type not in content
        assert label in content

    def test_status_code_never_verbatim(self):
        messages = service.build_moderation_messages(
            action_type="admin_reject",
            listing_title="Lagos Flat",
            listing_description="Two-bed flat",
            moderation_status="admin_review",
        )
        user_text = messages[1]["content"]
        assert "admin_review" not in user_text
        assert "admin review" in user_text

    def test_unknown_codes_humanized(self):
        messages = service.build_moderation_messages(
            action_type="some_new_action",
            listing_title="t",
            listing_description="d",
            moderation_status="some_new_status",
        )
        user_text = messages[1]["content"]
        assert "some_new_action" not in user_text
        assert "some_new_status" not in user_text
        assert "some new action" in user_text
        assert "some new status" in user_text

    def test_revoke_has_no_word_standalone(self):
        messages = service.build_moderation_messages(
            action_type="revoke",
            listing_title="t",
            listing_description="d",
            moderation_status="live",
        )
        content = "\n".join(m["content"] for m in messages)
        assert re.search(r"\brevoke\b", content) is None
        assert "revocation of a live listing" in content

    def test_reviewer_instructions_present(self):
        messages = service.build_moderation_messages(
            action_type="agency_reject",
            listing_title="t",
            listing_description="d",
            moderation_status="draft",
        )
        system_text = messages[0]["content"]
        assert "Do not mention internal identifiers" in system_text
        assert "say so plainly instead of drafting a reason" in system_text
        assert "Never advise the reviewer" in system_text

    def test_extra_context_and_delimiters_present(self):
        messages = service.build_moderation_messages(
            action_type="agency_reject",
            listing_title="Lagos Flat",
            listing_description="Two-bed flat",
            moderation_status="draft",
            listing_type="sale",
            location_name="Lagos, Nigeria",
            price_text="5000000 NGN",
        )
        user_text = messages[1]["content"]
        assert "Listing type: sale" in user_text
        assert "Location: Lagos, Nigeria" in user_text
        assert "Price: 5000000 NGN" in user_text
        assert "<<<UNTRUSTED_LISTING_TITLE" in user_text
        assert "<<<UNTRUSTED_LISTING_DESCRIPTION" in user_text
