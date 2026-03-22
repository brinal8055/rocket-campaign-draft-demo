from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import (
    BriefInput,
    CampaignPlan,
    DraftCreationResult,
    ResponsiveSearchAdVariant,
)


def build_valid_brief_input(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "product_name": "Rocket",
        "offer": "AI campaign drafting",
        "goal": "demo_bookings",
        "audience": "Growth leads at B2B SaaS startups",
        "geo": ["US"],
        "daily_budget_usd": 150.0,
        "landing_page_url": "https://example.com/demo",
        "tone": "Direct and credible",
        "brand_notes": "Avoid hype and keep claims grounded.",
    }
    payload.update(overrides)
    return payload


def build_valid_campaign_plan(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "campaign_name": "Rocket Demo Bookings | US | Search",
        "channel": "google_search",
        "keyword_themes": ["ai performance marketing", "startup demo booking ads"],
        "messaging_angles": ["Launch faster", "Keep ads paused until approval"],
        "utm_campaign": "rocket_demo_bookings_us_search",
        "geo_targets": ["US"],
        "recommended_daily_budget_usd": 150.0,
    }
    payload.update(overrides)
    return payload


def build_valid_rsa_variant(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "headlines": ["Book More Demos", "Launch Faster", "Keep Spend Controlled"],
        "descriptions": [
            "Turn a brief into a paused Google Ads draft.",
            "Keep approval in the loop before anything goes live.",
        ],
        "final_url": "https://example.com/demo",
        "path1": "demo",
        "path2": "book",
    }
    payload.update(overrides)
    return payload


def build_valid_draft_creation_result(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "campaign_resource_name": "customers/123/campaigns/456",
        "campaign_status": "PAUSED",
        "ad_group_resource_name": "customers/123/adGroups/789",
        "keyword_count": 5,
        "geo_target_count": 1,
        "approval_status": "PENDING",
    }
    payload.update(overrides)
    return payload


def test_brief_input_accepts_valid_payload() -> None:
    brief = BriefInput(**build_valid_brief_input())

    assert brief.geo == ["US"]
    assert brief.daily_budget_usd == 150.0


def test_campaign_plan_accepts_valid_payload() -> None:
    plan = CampaignPlan(**build_valid_campaign_plan())

    assert plan.geo_targets == ["US"]
    assert plan.recommended_daily_budget_usd == 150.0


@pytest.mark.parametrize("geo_value", [[], ["  "]])
def test_brief_input_rejects_empty_geo_values(geo_value: list[str]) -> None:
    with pytest.raises(ValidationError, match="geo"):
        BriefInput(**build_valid_brief_input(geo=geo_value))


@pytest.mark.parametrize("geo_targets_value", [[], ["  "]])
def test_campaign_plan_rejects_empty_geo_targets(geo_targets_value: list[str]) -> None:
    with pytest.raises(ValidationError, match="geo_targets"):
        CampaignPlan(**build_valid_campaign_plan(geo_targets=geo_targets_value))


@pytest.mark.parametrize("budget_value", [0, -1, -10.5])
def test_brief_input_rejects_non_positive_daily_budget(budget_value: float) -> None:
    with pytest.raises(ValidationError, match="daily_budget_usd"):
        BriefInput(**build_valid_brief_input(daily_budget_usd=budget_value))


@pytest.mark.parametrize("budget_value", [0, -1, -10.5])
def test_campaign_plan_rejects_non_positive_recommended_budget(budget_value: float) -> None:
    with pytest.raises(ValidationError, match="recommended_daily_budget_usd"):
        CampaignPlan(**build_valid_campaign_plan(recommended_daily_budget_usd=budget_value))


def test_responsive_search_ad_variant_accepts_minimum_required_assets() -> None:
    variant = ResponsiveSearchAdVariant(**build_valid_rsa_variant())

    assert len(variant.headlines) == 3
    assert len(variant.descriptions) == 2


def test_responsive_search_ad_variant_rejects_too_few_headlines() -> None:
    with pytest.raises(ValidationError, match="headlines"):
        ResponsiveSearchAdVariant(**build_valid_rsa_variant(headlines=["One", "Two"]))


def test_responsive_search_ad_variant_rejects_too_few_descriptions() -> None:
    with pytest.raises(ValidationError, match="descriptions"):
        ResponsiveSearchAdVariant(**build_valid_rsa_variant(descriptions=["Only one"]))


def test_responsive_search_ad_variant_rejects_headlines_longer_than_30_characters() -> None:
    with pytest.raises(ValidationError, match="30 characters"):
        ResponsiveSearchAdVariant(
            **build_valid_rsa_variant(
                headlines=[
                    "This headline is definitely too long",
                    "Launch Faster",
                    "Keep Spend Controlled",
                ]
            )
        )


def test_responsive_search_ad_variant_rejects_descriptions_longer_than_90_characters() -> None:
    with pytest.raises(ValidationError, match="90 characters"):
        ResponsiveSearchAdVariant(
            **build_valid_rsa_variant(
                descriptions=[
                    "This description is intentionally written to exceed ninety characters so validation catches it.",
                    "Keep approval in the loop before anything goes live.",
                ]
            )
        )


@pytest.mark.parametrize("field_name", ["path1", "path2"])
def test_responsive_search_ad_variant_rejects_display_paths_longer_than_15_characters(
    field_name: str,
) -> None:
    with pytest.raises(ValidationError, match="15 characters"):
        ResponsiveSearchAdVariant(
            **build_valid_rsa_variant(**{field_name: "this-path-is-too-long"})
        )


@pytest.mark.parametrize("final_url", ["", "   ", None])
def test_responsive_search_ad_variant_rejects_missing_final_url(final_url: object) -> None:
    with pytest.raises(ValidationError, match="final_url"):
        ResponsiveSearchAdVariant(**build_valid_rsa_variant(final_url=final_url))


def test_draft_creation_result_accepts_valid_payload() -> None:
    result = DraftCreationResult(**build_valid_draft_creation_result())

    assert result.campaign_status == "PAUSED"
    assert result.approval_status == "PENDING"


def test_draft_creation_result_requires_paused_status() -> None:
    with pytest.raises(ValidationError, match="campaign_status"):
        DraftCreationResult(**build_valid_draft_creation_result(campaign_status="ENABLED"))
