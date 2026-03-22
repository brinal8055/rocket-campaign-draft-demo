from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.schemas import CampaignPlan, DraftCreationResult, ResponsiveSearchAdVariant
from app.services.google_ads_service import ACCOUNT_METADATA_QUERY, GoogleAdsService, GoogleAdsServiceError
from app.utils import AppSettings


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
        google_ads_login_customer_id="9988776655",
        google_ads_use_test_account=True,
        n8n_approval_webhook_url="https://example.com/webhook/approval",
    )


def build_campaign_plan() -> CampaignPlan:
    return CampaignPlan(
        campaign_name="Rocket Demo Bookings | US | Search",
        channel="google_search",
        keyword_themes=["ai performance marketing", "startup demo booking ads"],
        messaging_angles=["Launch faster", "Keep approval in the loop"],
        utm_campaign="rocket_demo_bookings_us_search",
        geo_targets=["US"],
        recommended_daily_budget_amount=150.0,
        budget_currency_code="USD",
    )


def build_ad_variant() -> ResponsiveSearchAdVariant:
    return ResponsiveSearchAdVariant(
        headlines=["Book More Demos", "Launch Faster", "Keep Spend Controlled"],
        descriptions=[
            "Turn a brief into a paused Google Ads draft.",
            "Keep approval in the loop before anything goes live.",
        ],
        final_url="https://example.com/demo",
        path1="demo",
        path2="book",
    )


def test_from_settings_builds_google_ads_configuration() -> None:
    service = GoogleAdsService.from_settings(build_settings())

    assert service.customer_id == "1234567890"
    assert service.login_customer_id == "9988776655"
    assert service.require_test_account is True
    assert service._build_client_configuration() == {
        "developer_token": "developer-token",
        "client_id": "google-client-id",
        "client_secret": "google-client-secret",
        "refresh_token": "google-refresh-token",
        "login_customer_id": "9988776655",
        "use_proto_plus": True,
    }


def test_create_paused_draft_orchestrates_mutations(monkeypatch) -> None:
    service = GoogleAdsService(customer_id="1234567890", google_ads_client=object())
    call_order: list[tuple[str, object]] = []

    monkeypatch.setattr(
        GoogleAdsService,
        "get_account_metadata",
        lambda self: {
            "customer_id": "1234567890",
            "descriptive_name": "Rocket Test Account",
            "currency_code": "USD",
            "time_zone": "America/New_York",
            "is_manager": False,
            "is_test_account": True,
            "login_customer_id": "9988776655",
        },
    )

    monkeypatch.setattr(
        GoogleAdsService,
        "_create_campaign_budget",
        lambda self, **kwargs: call_order.append(("budget", kwargs["campaign_plan"].campaign_name))
        or "customers/123/campaignBudgets/111",
    )
    monkeypatch.setattr(
        GoogleAdsService,
        "_create_search_campaign",
        lambda self, **kwargs: call_order.append(("campaign", kwargs["campaign_budget_resource_name"]))
        or "customers/123/campaigns/222",
    )
    monkeypatch.setattr(
        GoogleAdsService,
        "_create_ad_group",
        lambda self, **kwargs: call_order.append(("ad_group", kwargs["campaign_resource_name"]))
        or "customers/123/adGroups/333",
    )
    monkeypatch.setattr(
        GoogleAdsService,
        "_add_keywords",
        lambda self, **kwargs: call_order.append(("keywords", tuple(kwargs["keywords"]))) or ["kw1", "kw2"],
    )
    monkeypatch.setattr(
        GoogleAdsService,
        "_add_geo_targeting",
        lambda self, **kwargs: call_order.append(("geo", tuple(kwargs["geo_targets"]))) or ["geo1"],
    )
    monkeypatch.setattr(
        GoogleAdsService,
        "_create_responsive_search_ad",
        lambda self, **kwargs: call_order.append(("rsa", kwargs["ad_group_resource_name"]))
        or "customers/123/adGroupAds/444",
    )

    result = service.create_paused_draft(
        campaign_plan=build_campaign_plan(),
        ad_variants=[build_ad_variant()],
    )

    assert result == DraftCreationResult(
        campaign_resource_name="customers/123/campaigns/222",
        campaign_status="PAUSED",
        ad_group_resource_name="customers/123/adGroups/333",
        campaign_budget_resource_name="customers/123/campaignBudgets/111",
        ad_group_ad_resource_name="customers/123/adGroupAds/444",
        keyword_count=2,
        geo_target_count=1,
        approval_status="PENDING",
        keyword_resource_names=["kw1", "kw2"],
        geo_target_resource_names=["geo1"],
        account_currency_code="USD",
    )
    assert call_order == [
        ("budget", "Rocket Demo Bookings | US | Search"),
        ("campaign", "customers/123/campaignBudgets/111"),
        ("ad_group", "customers/123/campaigns/222"),
        ("keywords", ("ai performance marketing", "startup demo booking ads")),
        ("geo", ("US",)),
        ("rsa", "customers/123/adGroups/333"),
    ]


def test_get_account_metadata_runs_read_only_query() -> None:
    class FakeGoogleAdsQueryService:
        def __init__(self) -> None:
            self.search_calls: list[dict[str, object]] = []

        def search(self, *, customer_id: str, query: str):
            self.search_calls.append({"customer_id": customer_id, "query": query})
            row = SimpleNamespace(
                customer=SimpleNamespace(
                    id=1234567890,
                    descriptive_name="Rocket Test Account",
                    currency_code="USD",
                    time_zone="America/New_York",
                    manager=False,
                    test_account=True,
                )
            )
            return [row]

    fake_query_service = FakeGoogleAdsQueryService()
    fake_client = SimpleNamespace(
        get_service=lambda name: fake_query_service if name == "GoogleAdsService" else None
    )
    service = GoogleAdsService(
        customer_id="1234567890",
        login_customer_id="9988776655",
        google_ads_client=fake_client,
    )

    metadata = service.get_account_metadata()

    assert metadata == {
        "customer_id": "1234567890",
        "descriptive_name": "Rocket Test Account",
        "currency_code": "USD",
        "time_zone": "America/New_York",
        "is_manager": False,
        "is_test_account": True,
        "login_customer_id": "9988776655",
    }
    assert fake_query_service.search_calls == [
        {"customer_id": "1234567890", "query": ACCOUNT_METADATA_QUERY}
    ]


def test_create_paused_draft_rejects_non_test_account_when_required(monkeypatch) -> None:
    service = GoogleAdsService(customer_id="1234567890", google_ads_client=object(), require_test_account=True)
    monkeypatch.setattr(
        GoogleAdsService,
        "get_account_metadata",
        lambda self: {
            "customer_id": "1234567890",
            "descriptive_name": "Rocket Prod Account",
            "currency_code": "USD",
            "time_zone": "America/New_York",
            "is_manager": False,
            "is_test_account": False,
            "login_customer_id": "9988776655",
        },
    )

    with pytest.raises(GoogleAdsServiceError, match="requires a Google Ads test account"):
        service.create_paused_draft(
            campaign_plan=build_campaign_plan(),
            ad_variants=[build_ad_variant()],
        )


def test_create_paused_draft_rejects_budget_currency_mismatch(monkeypatch) -> None:
    service = GoogleAdsService(customer_id="1234567890", google_ads_client=object(), require_test_account=False)
    monkeypatch.setattr(
        GoogleAdsService,
        "get_account_metadata",
        lambda self: {
            "customer_id": "1234567890",
            "descriptive_name": "Rocket Test Account",
            "currency_code": "INR",
            "time_zone": "Asia/Kolkata",
            "is_manager": False,
            "is_test_account": True,
            "login_customer_id": "9988776655",
        },
    )

    with pytest.raises(GoogleAdsServiceError, match="Budget currency: USD. Account currency: INR"):
        service.create_paused_draft(
            campaign_plan=build_campaign_plan(),
            ad_variants=[build_ad_variant()],
        )


def test_create_paused_draft_attempts_cleanup_and_exposes_partial_state(monkeypatch) -> None:
    service = GoogleAdsService(customer_id="1234567890", google_ads_client=object(), require_test_account=False)
    captured_cleanup_state: dict[str, object] = {}

    monkeypatch.setattr(
        GoogleAdsService,
        "get_account_metadata",
        lambda self: {
            "customer_id": "1234567890",
            "descriptive_name": "Rocket Test Account",
            "currency_code": "USD",
            "time_zone": "America/New_York",
            "is_manager": False,
            "is_test_account": True,
            "login_customer_id": "9988776655",
        },
    )
    monkeypatch.setattr(
        GoogleAdsService,
        "_create_campaign_budget",
        lambda self, **kwargs: "customers/123/campaignBudgets/111",
    )
    monkeypatch.setattr(
        GoogleAdsService,
        "_create_search_campaign",
        lambda self, **kwargs: (_ for _ in ()).throw(GoogleAdsServiceError("campaign create failed")),
    )
    monkeypatch.setattr(
        GoogleAdsService,
        "_cleanup_partial_draft",
        lambda self, *, client, draft_state: captured_cleanup_state.update(draft_state.to_dict()) or None,
    )

    with pytest.raises(GoogleAdsServiceError, match="campaign create failed") as exc_info:
        service.create_paused_draft(
            campaign_plan=build_campaign_plan(),
            ad_variants=[build_ad_variant()],
        )

    assert captured_cleanup_state["campaign_budget_resource_name"] == "customers/123/campaignBudgets/111"
    assert exc_info.value.partial_state is not None
    assert exc_info.value.partial_state.campaign_budget_resource_name == "customers/123/campaignBudgets/111"


def test_create_search_campaign_disables_content_network() -> None:
    campaign_service_calls: list[dict[str, object]] = []

    class FakeCampaignService:
        def mutate_campaigns(self, *, customer_id: str, operations: list[object]):
            campaign_service_calls.append({"customer_id": customer_id, "operations": operations})
            return SimpleNamespace(
                results=[SimpleNamespace(resource_name="customers/123/campaigns/222")]
            )

    class FakeClient:
        enums = SimpleNamespace(
            AdvertisingChannelTypeEnum=SimpleNamespace(SEARCH="SEARCH"),
            CampaignStatusEnum=SimpleNamespace(PAUSED="PAUSED"),
            EuPoliticalAdvertisingStatusEnum=SimpleNamespace(
                DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING="DOES_NOT_CONTAIN"
            ),
        )

        def get_service(self, name: str):
            assert name == "CampaignService"
            return FakeCampaignService()

        def get_type(self, name: str):
            if name == "CampaignOperation":
                return SimpleNamespace(
                    create=SimpleNamespace(
                        network_settings=SimpleNamespace(),
                    )
                )
            if name == "ManualCpc":
                return SimpleNamespace()
            raise AssertionError(f"Unexpected type request: {name}")

    service = GoogleAdsService(customer_id="1234567890", google_ads_client=FakeClient())

    resource_name = service._create_search_campaign(
        client=FakeClient(),
        campaign_plan=build_campaign_plan(),
        campaign_budget_resource_name="customers/123/campaignBudgets/111",
    )

    assert resource_name == "customers/123/campaigns/222"
    campaign_operation = campaign_service_calls[0]["operations"][0]
    assert campaign_operation.create.network_settings.target_content_network is False
