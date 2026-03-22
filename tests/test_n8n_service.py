from __future__ import annotations

from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest
from pydantic import SecretStr

from app.schemas import ResponsiveSearchAdVariant
from app.services.n8n_service import N8NService, N8NServiceError
from app.utils import AppSettings


class FakeHTTPResponse:
    def __init__(self, status: int = 200) -> None:
        self.status = status


def build_settings() -> AppSettings:
    return AppSettings(
        environment="local",
        log_level="INFO",
        openai_api_key=SecretStr("openai-key"),
        openai_strategy_model="gpt-5.4",
        openai_transform_model="gpt-5-mini",
        openai_request_timeout_seconds=60.0,
        langsmith_api_key=SecretStr("langsmith-key"),
        langsmith_project="rocket-demo-tests",
        google_ads_developer_token=SecretStr("developer-token"),
        google_ads_client_id="google-client-id",
        google_ads_client_secret=SecretStr("google-client-secret"),
        google_ads_refresh_token=SecretStr("google-refresh-token"),
        google_ads_customer_id="1234567890",
        google_ads_use_test_account=True,
        n8n_approval_webhook_url="https://example.com/webhook/approval",
    )


def build_ad_variants() -> list[ResponsiveSearchAdVariant]:
    return [
        ResponsiveSearchAdVariant(
            headlines=["Book More Demos", "Launch Faster", "Keep Spend Controlled"],
            descriptions=[
                "Turn a brief into a paused Google Ads draft.",
                "Keep approval in the loop before anything goes live.",
            ],
            final_url="https://example.com/demo",
            path1="demo",
            path2="book",
        ),
        ResponsiveSearchAdVariant(
            headlines=["Pause Before Launch", "Draft Search Campaigns", "Ship Campaign Ops Faster"],
            descriptions=[
                "Create a structured draft straight from the brief.",
                "Route approval through the right team before publishing.",
            ],
            final_url="https://example.com/demo",
            path1="campaign",
            path2="draft",
        ),
    ]


def test_from_settings_uses_webhook_url() -> None:
    service = N8NService.from_settings(build_settings())

    assert service.webhook_url == "https://example.com/webhook/approval"


def test_build_campaign_draft_payload_contains_required_fields() -> None:
    service = N8NService(webhook_url="https://example.com/webhook/approval")

    payload = service.build_campaign_draft_payload(
        campaign_name="Rocket Demo Campaign",
        customer_id="1234567890",
        campaign_resource_name="customers/123/campaigns/456",
        campaign_status="PAUSED",
        landing_page_url="https://example.com/demo",
        daily_budget=150.0,
        keyword_themes=["ai performance marketing", "startup demo booking ads"],
        ad_variants=build_ad_variants(),
    )

    assert payload == {
        "campaign_name": "Rocket Demo Campaign",
        "customer_id": "1234567890",
        "campaign_resource_name": "customers/123/campaigns/456",
        "campaign_status": "PAUSED",
        "landing_page_url": "https://example.com/demo",
        "daily_budget": 150.0,
        "keyword_themes": ["ai performance marketing", "startup demo booking ads"],
        "ad_copy_summary": (
            "V1: H=Book More Demos / Launch Faster / Keep Spend Controlled; "
            "D=Turn a brief into a paused Google Ads draft. / Keep approval in the loop before anything goes live. "
            "|| V2: H=Pause Before Launch / Draft Search Campaigns / Ship Campaign Ops Faster; "
            "D=Create a structured draft straight from the brief. / Route approval through the right team before publishing."
        ),
    }


def test_request_approval_posts_json_payload() -> None:
    sent = {}

    def fake_sender(request, *, timeout: float):
        sent["url"] = request.full_url
        sent["timeout"] = timeout
        sent["headers"] = dict(request.header_items())
        sent["body"] = request.data.decode("utf-8")
        return FakeHTTPResponse(status=200)

    service = N8NService(
        webhook_url="https://example.com/webhook/approval",
        request_sender=fake_sender,
    )

    service.request_approval({"campaign_name": "Rocket Demo Campaign"})

    assert sent["url"] == "https://example.com/webhook/approval"
    assert sent["timeout"] == 10.0
    assert sent["headers"]["Content-type"] == "application/json"
    assert sent["body"] == '{"campaign_name": "Rocket Demo Campaign"}'


def test_request_approval_retries_once_on_transient_http_failure() -> None:
    attempts = {"count": 0}

    def fake_sender(request, *, timeout: float):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise HTTPError(
                url=request.full_url,
                code=503,
                msg="Service Unavailable",
                hdrs=None,
                fp=BytesIO(b"temporary failure"),
            )
        return FakeHTTPResponse(status=200)

    service = N8NService(
        webhook_url="https://example.com/webhook/approval",
        request_sender=fake_sender,
    )

    service.request_approval({"campaign_name": "Rocket Demo Campaign"})

    assert attempts["count"] == 2


def test_request_approval_raises_clean_error_when_unreachable() -> None:
    attempts = {"count": 0}

    def fake_sender(request, *, timeout: float):
        attempts["count"] += 1
        raise URLError("temporary DNS failure")

    service = N8NService(
        webhook_url="https://example.com/webhook/approval",
        request_sender=fake_sender,
    )

    with pytest.raises(N8NServiceError, match="n8n approval webhook is unreachable"):
        service.request_approval({"campaign_name": "Rocket Demo Campaign"})

    assert attempts["count"] == 2


def test_request_approval_raises_clean_error_on_non_transient_http_failure() -> None:
    def fake_sender(request, *, timeout: float):
        raise HTTPError(
            url=request.full_url,
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=BytesIO(b"invalid payload"),
        )

    service = N8NService(
        webhook_url="https://example.com/webhook/approval",
        request_sender=fake_sender,
    )

    with pytest.raises(
        N8NServiceError,
        match="n8n approval webhook rejected the request with HTTP 400",
    ):
        service.request_approval({"campaign_name": "Rocket Demo Campaign"})
