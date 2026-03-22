from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.agents import BriefParser, RSACopyGenerator, StrategyComposer
from app.schemas import (
    BriefInput,
    DemoRunArtifact,
    CampaignPlan,
    DemoRunResult,
    DemoRunStatus,
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
from app.services.google_ads_service import GoogleAdsServiceError
from app.services.n8n_service import N8NServiceError
from app.services.openai_client import OpenAIResponseError
from app.utils import AppSettings, mask_identifier, sensitive_observability_enabled, traceable
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


def _sanitize_flow_trace_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    if sensitive_observability_enabled():
        return inputs

    raw_brief = inputs.get("raw_brief")
    raw_brief_keys = sorted(raw_brief.keys()) if isinstance(raw_brief, Mapping) else []
    return {
        "artifact_path": str(inputs.get("artifact_path", "artifacts/last_run.json")),
        "rsa_variant_count": inputs.get("rsa_variant_count", 3),
        "raw_brief_keys": raw_brief_keys,
    }


def _sanitize_flow_trace_outputs(output: Any) -> Any:
    if sensitive_observability_enabled():
        return output

    if isinstance(output, DemoRunResult):
        return {
            "campaign_name": output.campaign_plan.campaign_name,
            "campaign_status": output.draft_creation_result.campaign_status,
            "campaign_resource_name": mask_identifier(
                output.draft_creation_result.campaign_resource_name,
                visible_suffix=8,
            ),
            "approval_status": output.draft_creation_result.approval_status,
        }
    return output


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

    @traceable(
        run_type="chain",
        name="rocket_demo_flow",
        process_inputs=_sanitize_flow_trace_inputs,
        process_outputs=_sanitize_flow_trace_outputs,
        exceptions_to_handle=(
            DemoFlowError,
            GoogleAdsServiceError,
            N8NServiceError,
            OpenAIResponseError,
            ValueError,
        ),
    )
    def run(
        self,
        *,
        raw_brief: Mapping[str, Any],
        artifact_path: str | Path = "artifacts/last_run.json",
        rsa_variant_count: int = 3,
    ) -> DemoRunResult:
        output_path = Path(artifact_path)
        completed_stages = [self._complete_stage(PipelineStageName.BRIEF_LOAD)]
        current_stage = PipelineStageName.BRIEF_NORMALIZATION
        brief: BriefInput | None = None
        campaign_plan: CampaignPlan | None = None
        rsa_variants: list[ResponsiveSearchAdVariant] = []
        draft_creation_result: DraftCreationResult | None = None
        approval_payload: dict[str, Any] | None = None

        self._save_checkpoint(
            artifact_path=output_path,
            run_status=DemoRunStatus.IN_PROGRESS,
            stages=completed_stages,
        )

        try:
            brief = self._brief_parser().parse(raw_brief)
            completed_stages.append(self._complete_stage(PipelineStageName.BRIEF_NORMALIZATION))
            self._save_checkpoint(
                artifact_path=output_path,
                run_status=DemoRunStatus.IN_PROGRESS,
                stages=completed_stages,
                brief=brief,
            )

            current_stage = PipelineStageName.STRATEGY_GENERATION
            campaign_plan = self._strategy_composer().compose(brief)
            completed_stages.append(self._complete_stage(PipelineStageName.STRATEGY_GENERATION))
            self._save_checkpoint(
                artifact_path=output_path,
                run_status=DemoRunStatus.IN_PROGRESS,
                stages=completed_stages,
                brief=brief,
                campaign_plan=campaign_plan,
            )

            current_stage = PipelineStageName.COPY_GENERATION
            rsa_variants = self._rsa_copy_generator().generate(
                brief=brief,
                campaign_plan=campaign_plan,
                variant_count=rsa_variant_count,
            )
            completed_stages.append(self._complete_stage(PipelineStageName.COPY_GENERATION))
            self._save_checkpoint(
                artifact_path=output_path,
                run_status=DemoRunStatus.IN_PROGRESS,
                stages=completed_stages,
                brief=brief,
                campaign_plan=campaign_plan,
                rsa_variants=rsa_variants,
            )

            current_stage = PipelineStageName.VALIDATION
            validated_brief, validated_campaign_plan, validated_rsa_variants = self._validate_outputs(
                brief=brief,
                campaign_plan=campaign_plan,
                rsa_variants=rsa_variants,
            )
            brief = validated_brief
            campaign_plan = validated_campaign_plan
            rsa_variants = validated_rsa_variants
            completed_stages.append(self._complete_stage(PipelineStageName.VALIDATION))
            self._save_checkpoint(
                artifact_path=output_path,
                run_status=DemoRunStatus.IN_PROGRESS,
                stages=completed_stages,
                brief=brief,
                campaign_plan=campaign_plan,
                rsa_variants=rsa_variants,
            )

            current_stage = PipelineStageName.GOOGLE_ADS_DRAFT
            draft_creation_result = ensure_paused_campaign_result(
                self._google_ads_service().create_paused_draft(
                    campaign_plan=campaign_plan,
                    ad_variants=rsa_variants,
                )
            )
            completed_stages.append(self._complete_stage(PipelineStageName.GOOGLE_ADS_DRAFT))
            self._save_checkpoint(
                artifact_path=output_path,
                run_status=DemoRunStatus.IN_PROGRESS,
                stages=completed_stages,
                brief=brief,
                campaign_plan=campaign_plan,
                rsa_variants=rsa_variants,
                draft_creation_result=draft_creation_result,
            )

            current_stage = PipelineStageName.APPROVAL_REQUEST
            approval_payload = self._n8n_service().send_campaign_draft_for_approval(
                campaign_name=campaign_plan.campaign_name,
                customer_id=self.settings.google_ads_customer_id,
                campaign_resource_name=draft_creation_result.campaign_resource_name,
                campaign_status=draft_creation_result.campaign_status,
                landing_page_url=str(brief.landing_page_url),
                daily_budget=campaign_plan.recommended_daily_budget_amount,
                daily_budget_currency=campaign_plan.budget_currency_code,
                keyword_themes=campaign_plan.keyword_themes,
                ad_variants=rsa_variants,
            )
            completed_stages.append(self._complete_stage(PipelineStageName.APPROVAL_REQUEST))

            result = DemoRunResult(
                environment=self.settings.environment,
                brief=brief,
                campaign_plan=campaign_plan,
                rsa_variants=rsa_variants,
                draft_creation_result=draft_creation_result,
                approval_payload=approval_payload,
                artifact_path=str(output_path),
                stages=completed_stages,
            )
            self._save_artifact(
                artifact=self._build_artifact(
                    artifact_path=output_path,
                    run_status=DemoRunStatus.COMPLETED,
                    stages=completed_stages,
                    brief=brief,
                    campaign_plan=campaign_plan,
                    rsa_variants=rsa_variants,
                    draft_creation_result=draft_creation_result,
                    approval_payload=approval_payload,
                )
            )
            return result
        except Exception as exc:
            failed_stages = list(completed_stages)
            if not any(stage.name == current_stage for stage in failed_stages):
                failed_stages.append(self._failed_stage(current_stage))

            partial_state = getattr(exc, "partial_state", None)
            partial_draft_state = (
                partial_state.to_dict()
                if partial_state is not None and hasattr(partial_state, "to_dict")
                else None
            )
            self._save_artifact(
                artifact=self._build_artifact(
                    artifact_path=output_path,
                    run_status=DemoRunStatus.FAILED,
                    stages=failed_stages,
                    brief=brief,
                    campaign_plan=campaign_plan,
                    rsa_variants=rsa_variants,
                    draft_creation_result=draft_creation_result,
                    partial_draft_state=partial_draft_state,
                    approval_payload=approval_payload,
                    error_message=str(exc),
                )
            )
            raise

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

        if validated_campaign_plan.budget_currency_code != validated_brief.budget_currency_code:
            raise DemoFlowError(
                "Campaign plan budget_currency_code must match the validated brief budget_currency_code."
            )

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
        artifact: DemoRunArtifact,
    ) -> None:
        output_path = Path(artifact.artifact_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            artifact.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def _save_checkpoint(
        self,
        *,
        artifact_path: Path,
        run_status: DemoRunStatus,
        stages: list[StageExecutionResult],
        brief: BriefInput | None = None,
        campaign_plan: CampaignPlan | None = None,
        rsa_variants: list[ResponsiveSearchAdVariant] | None = None,
        draft_creation_result: DraftCreationResult | None = None,
        partial_draft_state: dict[str, Any] | None = None,
        approval_payload: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        self._save_artifact(
            artifact=self._build_artifact(
                artifact_path=artifact_path,
                run_status=run_status,
                stages=stages,
                brief=brief,
                campaign_plan=campaign_plan,
                rsa_variants=rsa_variants,
                draft_creation_result=draft_creation_result,
                partial_draft_state=partial_draft_state,
                approval_payload=approval_payload,
                error_message=error_message,
            )
        )

    def _build_artifact(
        self,
        *,
        artifact_path: Path,
        run_status: DemoRunStatus,
        stages: list[StageExecutionResult],
        brief: BriefInput | None = None,
        campaign_plan: CampaignPlan | None = None,
        rsa_variants: list[ResponsiveSearchAdVariant] | None = None,
        draft_creation_result: DraftCreationResult | None = None,
        partial_draft_state: dict[str, Any] | None = None,
        approval_payload: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> DemoRunArtifact:
        return DemoRunArtifact(
            environment=self.settings.environment,
            artifact_path=str(artifact_path),
            run_status=run_status,
            stages=list(stages),
            brief=brief,
            campaign_plan=campaign_plan,
            rsa_variants=list(rsa_variants or []),
            draft_creation_result=draft_creation_result,
            partial_draft_state=partial_draft_state,
            approval_payload=approval_payload,
            error_message=error_message,
        )

    def _complete_stage(self, name: PipelineStageName) -> StageExecutionResult:
        return StageExecutionResult(
            name=name,
            description=PIPELINE_STAGE_DESCRIPTIONS[name],
            status=PipelineStageStatus.COMPLETED,
        )

    def _failed_stage(self, name: PipelineStageName) -> StageExecutionResult:
        return StageExecutionResult(
            name=name,
            description=PIPELINE_STAGE_DESCRIPTIONS[name],
            status=PipelineStageStatus.FAILED,
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
