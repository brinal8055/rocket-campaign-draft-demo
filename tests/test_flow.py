from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.orchestration.flow import DemoFlowError, RocketDemoFlow
from app.schemas import BriefInput, CampaignPlan, DraftCreationResult, ResponsiveSearchAdVariant
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
        google_ads_use_test_account=True,
        n8n_approval_webhook_url="https://example.com/webhook/approval",
    )


def build_brief() -> BriefInput:
    return BriefInput(
        product_name="Rocket",
        offer="AI campaign drafting",
        goal="demo_bookings",
        audience="Growth leads at B2B SaaS companies",
        geo=["US"],
        daily_budget_usd=150.0,
        landing_page_url="https://example.com/demo",
        tone="Direct and credible",
        brand_notes="Avoid hype and keep it specific.",
    )


def build_campaign_plan() -> CampaignPlan:
    return CampaignPlan(
        campaign_name="Rocket Demo Bookings | US | Search",
        channel="google_search",
        keyword_themes=["ai performance marketing", "startup demo booking ads"],
        messaging_angles=["Launch faster", "Keep approval in the loop"],
        utm_campaign="rocket_demo_bookings_us_search",
        geo_targets=["United States"],
        recommended_daily_budget_usd=150.0,
    )


def build_rsa_variants() -> list[ResponsiveSearchAdVariant]:
    return [
        ResponsiveSearchAdVariant(
            headlines=[f"Book More Demos {index}", f"Launch Faster {index}", f"Keep Spend Controlled {index}"],
            descriptions=[
                f"Turn a brief into a paused Google Ads draft {index}.",
                f"Keep approval in the loop before anything goes live {index}.",
            ],
            final_url="https://example.com/demo",
            path1="demo",
            path2="book",
        )
        for index in range(1, 4)
    ]


@dataclass
class FakeBriefParser:
    response: BriefInput

    def parse(self, raw_brief):
        return self.response


@dataclass
class FakeStrategyComposer:
    response: CampaignPlan

    def compose(self, brief: BriefInput):
        return self.response


@dataclass
class FakeRSACopyGenerator:
    response: list[ResponsiveSearchAdVariant]

    def generate(self, brief: BriefInput, campaign_plan: CampaignPlan, *, variant_count: int = 3):
        return self.response


@dataclass
class FakeGoogleAdsService:
    response: DraftCreationResult

    def create_paused_draft(self, campaign_plan: CampaignPlan, ad_variants: list[ResponsiveSearchAdVariant]):
        return self.response


@dataclass
class FakeN8NService:
    payload: dict[str, object] | None = None

    def send_campaign_draft_for_approval(self, **kwargs):
        self.payload = kwargs
        return kwargs


def test_flow_runs_end_to_end_and_saves_artifact(tmp_path: Path) -> None:
    brief = build_brief()
    campaign_plan = build_campaign_plan()
    rsa_variants = build_rsa_variants()
    draft_result = DraftCreationResult(
        campaign_resource_name="customers/123/campaigns/456",
        campaign_status="PAUSED",
        ad_group_resource_name="customers/123/adGroups/789",
        keyword_count=2,
        geo_target_count=1,
        approval_status="PENDING",
    )
    fake_n8n_service = FakeN8NService()

    artifact_path = tmp_path / "artifacts" / "last_run.json"
    flow = RocketDemoFlow(
        settings=build_settings(),
        brief_parser=FakeBriefParser(response=brief),
        strategy_composer=FakeStrategyComposer(response=campaign_plan),
        rsa_copy_generator=FakeRSACopyGenerator(response=rsa_variants),
        google_ads_service=FakeGoogleAdsService(response=draft_result),
        n8n_service=fake_n8n_service,
    )

    result = flow.run(
        raw_brief={"offer": "AI campaign drafting"},
        artifact_path=artifact_path,
        rsa_variant_count=3,
    )

    assert result.draft_creation_result == draft_result
    assert result.approval_payload["campaign_name"] == campaign_plan.campaign_name
    assert result.artifact_path == str(artifact_path)
    assert artifact_path.exists()

    saved_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert saved_payload["draft_creation_result"]["campaign_status"] == "PAUSED"
    assert saved_payload["approval_payload"]["customer_id"] == "1234567890"
    assert [stage["name"] for stage in saved_payload["stages"]] == [
        "brief_load",
        "brief_normalization",
        "strategy_generation",
        "copy_generation",
        "validation",
        "google_ads_draft",
        "approval_request",
    ]


def test_flow_rejects_variant_final_url_that_does_not_match_brief() -> None:
    invalid_variant = ResponsiveSearchAdVariant(
        headlines=["Book More Demos", "Launch Faster", "Keep Spend Controlled"],
        descriptions=[
            "Turn a brief into a paused Google Ads draft.",
            "Keep approval in the loop before anything goes live.",
        ],
        final_url="https://example.com/other",
        path1="demo",
        path2="book",
    )
    flow = RocketDemoFlow(
        settings=build_settings(),
        brief_parser=FakeBriefParser(response=build_brief()),
        strategy_composer=FakeStrategyComposer(response=build_campaign_plan()),
        rsa_copy_generator=FakeRSACopyGenerator(response=[invalid_variant, *build_rsa_variants()[1:]]),
        google_ads_service=FakeGoogleAdsService(
            response=DraftCreationResult(
                campaign_resource_name="customers/123/campaigns/456",
                campaign_status="PAUSED",
                ad_group_resource_name="customers/123/adGroups/789",
                keyword_count=2,
                geo_target_count=1,
                approval_status="PENDING",
            )
        ),
        n8n_service=FakeN8NService(),
    )

    with pytest.raises(
        DemoFlowError,
        match="RSA variant 1 final_url must match the validated brief landing_page_url",
    ):
        flow.run(raw_brief={"offer": "AI campaign drafting"})
