from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.agents import BriefParser, RSACopyGenerator, StrategyComposer
from app.schemas import (
    BriefInput,
    CampaignPlan,
    DemoRunResult,
    DraftCreationResult,
    PipelineStageName,
    PipelineStageStatus,
    ResponsiveSearchAdVariant,
    StageExecutionResult,
)
from app.services import (
    GoogleAdsService,
    N8NService,
    ObservabilityService,
    OpenAIResponsesClient,
)
from app.utils import AppSettings, traceable
from app.validators import ensure_paused_campaign_result

PIPELINE_STAGE_DESCRIPTIONS: dict[PipelineStageName, str] = {
    PipelineStageName.BRIEF_LOAD: "Load the source brief JSON from disk.",
    PipelineStageName.BRIEF_NORMALIZATION: "Normalize raw operator input into a typed brief.",
    PipelineStageName.STRATEGY_GENERATION: "Produce campaign naming, targeting, and keyword themes.",
    PipelineStageName.COPY_GENERATION: "Produce responsive search ad variants for review.",
    PipelineStageName.VALIDATION: "Validate generated outputs before any external mutation.",
    PipelineStageName.GOOGLE_ADS_DRAFT: "Create the paused draft campaign and related assets.",
    PipelineStageName.APPROVAL_REQUEST: "Send the draft summary to the approval channel.",
}

GEO_ALIASES = {
    "UK": "UNITED KINGDOM",
    "UAE": "UNITED ARAB EMIRATES",
    "US": "UNITED STATES",
    "USA": "UNITED STATES",
}


class DemoFlowError(RuntimeError):
    """Raised when the end-to-end demo flow cannot complete successfully."""


@dataclass(slots=True)
class RocketDemoFlow:
    """Run the first end-to-end demo wedge for Rocket."""

    settings: AppSettings
    brief_parser: BriefParser | None = None
    strategy_composer: StrategyComposer | None = None
    rsa_copy_generator: RSACopyGenerator | None = None
    google_ads_service: GoogleAdsService | None = None
    n8n_service: N8NService | None = None
    observability_service: ObservabilityService | None = None

    def __post_init__(self) -> None:
        if self.observability_service is None:
            self.observability_service = ObservabilityService.from_settings(self.settings)
        self._observability_service().configure()

        openai_client = OpenAIResponsesClient.from_settings(self.settings)

        if self.brief_parser is None:
            self.brief_parser = BriefParser(openai_client=openai_client)
        if self.strategy_composer is None:
            self.strategy_composer = StrategyComposer(openai_client=openai_client)
        if self.rsa_copy_generator is None:
            self.rsa_copy_generator = RSACopyGenerator(openai_client=openai_client)
        if self.google_ads_service is None:
            self.google_ads_service = GoogleAdsService.from_settings(self.settings)
        if self.n8n_service is None:
            self.n8n_service = N8NService.from_settings(self.settings)

    @traceable(run_type="chain", name="rocket_demo_flow")
    def run(
        self,
        *,
        raw_brief: Mapping[str, Any],
        artifact_path: str | Path = "artifacts/last_run.json",
        rsa_variant_count: int = 3,
    ) -> DemoRunResult:
        completed_stages = [
            self._complete_stage(PipelineStageName.BRIEF_LOAD),
        ]

        brief = self._brief_parser().parse(raw_brief)
        completed_stages.append(self._complete_stage(PipelineStageName.BRIEF_NORMALIZATION))

        campaign_plan = self._strategy_composer().compose(brief)
        completed_stages.append(self._complete_stage(PipelineStageName.STRATEGY_GENERATION))

        rsa_variants = self._rsa_copy_generator().generate(
            brief=brief,
            campaign_plan=campaign_plan,
            variant_count=rsa_variant_count,
        )
        completed_stages.append(self._complete_stage(PipelineStageName.COPY_GENERATION))

        validated_brief, validated_campaign_plan, validated_rsa_variants = self._validate_outputs(
            brief=brief,
            campaign_plan=campaign_plan,
            rsa_variants=rsa_variants,
        )
        completed_stages.append(self._complete_stage(PipelineStageName.VALIDATION))

        draft_creation_result = ensure_paused_campaign_result(
            self._google_ads_service().create_paused_draft(
                campaign_plan=validated_campaign_plan,
                ad_variants=validated_rsa_variants,
            )
        )
        completed_stages.append(self._complete_stage(PipelineStageName.GOOGLE_ADS_DRAFT))

        approval_payload = self._n8n_service().send_campaign_draft_for_approval(
            campaign_name=validated_campaign_plan.campaign_name,
            customer_id=self.settings.google_ads_customer_id,
            campaign_resource_name=draft_creation_result.campaign_resource_name,
            campaign_status=draft_creation_result.campaign_status,
            landing_page_url=str(validated_brief.landing_page_url),
            daily_budget=validated_campaign_plan.recommended_daily_budget_usd,
            keyword_themes=validated_campaign_plan.keyword_themes,
            ad_variants=validated_rsa_variants,
        )
        completed_stages.append(self._complete_stage(PipelineStageName.APPROVAL_REQUEST))

        result = DemoRunResult(
            environment=self.settings.environment,
            brief=validated_brief,
            campaign_plan=validated_campaign_plan,
            rsa_variants=validated_rsa_variants,
            draft_creation_result=draft_creation_result,
            approval_payload=approval_payload,
            artifact_path=str(Path(artifact_path)),
            stages=completed_stages,
        )
        self._save_artifact(result=result, artifact_path=artifact_path)
        return result

    def _validate_outputs(
        self,
        *,
        brief: BriefInput,
        campaign_plan: CampaignPlan,
        rsa_variants: list[ResponsiveSearchAdVariant],
    ) -> tuple[BriefInput, CampaignPlan, list[ResponsiveSearchAdVariant]]:
        validated_brief = BriefInput.model_validate(brief.model_dump(mode="json"))
        validated_campaign_plan = CampaignPlan.model_validate(campaign_plan.model_dump(mode="json"))
        validated_rsa_variants = [
            ResponsiveSearchAdVariant.model_validate(ad_variant.model_dump(mode="json"))
            for ad_variant in rsa_variants
        ]

        if len(validated_rsa_variants) < 3:
            raise DemoFlowError("At least 3 RSA variants are required for the demo flow.")

        brief_geo = {self._normalize_geo(value) for value in validated_brief.geo}
        plan_geo = {self._normalize_geo(value) for value in validated_campaign_plan.geo_targets}
        if not plan_geo.issubset(brief_geo):
            raise DemoFlowError("Campaign plan geo_targets must stay aligned with the validated brief.")

        expected_landing_page_url = str(validated_brief.landing_page_url)
        for index, ad_variant in enumerate(validated_rsa_variants, start=1):
            if str(ad_variant.final_url) != expected_landing_page_url:
                raise DemoFlowError(
                    f"RSA variant {index} final_url must match the validated brief landing_page_url."
                )

        return validated_brief, validated_campaign_plan, validated_rsa_variants

    def _save_artifact(
        self,
        *,
        result: DemoRunResult,
        artifact_path: str | Path,
    ) -> None:
        output_path = Path(artifact_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            result.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def _complete_stage(self, name: PipelineStageName) -> StageExecutionResult:
        return StageExecutionResult(
            name=name,
            description=PIPELINE_STAGE_DESCRIPTIONS[name],
            status=PipelineStageStatus.COMPLETED,
        )

    def _normalize_geo(self, value: str) -> str:
        normalized = " ".join(value.strip().split()).upper()
        return GEO_ALIASES.get(normalized, normalized)

    def _brief_parser(self) -> BriefParser:
        if self.brief_parser is None:
            raise DemoFlowError("Brief parser is not configured.")
        return self.brief_parser

    def _strategy_composer(self) -> StrategyComposer:
        if self.strategy_composer is None:
            raise DemoFlowError("Strategy composer is not configured.")
        return self.strategy_composer

    def _rsa_copy_generator(self) -> RSACopyGenerator:
        if self.rsa_copy_generator is None:
            raise DemoFlowError("RSA copy generator is not configured.")
        return self.rsa_copy_generator

    def _google_ads_service(self) -> GoogleAdsService:
        if self.google_ads_service is None:
            raise DemoFlowError("Google Ads service is not configured.")
        return self.google_ads_service

    def _n8n_service(self) -> N8NService:
        if self.n8n_service is None:
            raise DemoFlowError("n8n service is not configured.")
        return self.n8n_service

    def _observability_service(self) -> ObservabilityService:
        if self.observability_service is None:
            raise DemoFlowError("Observability service is not configured.")
        return self.observability_service
