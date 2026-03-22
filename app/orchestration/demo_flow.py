from __future__ import annotations

from dataclasses import dataclass, field

from app.agents import BriefParser, CopyGenerator, StrategyComposer
from app.schemas.runtime import DemoScaffoldSummary, PipelineStageName, StagePreview
from app.services import ApprovalService, GoogleAdsService, ObservabilityService
from app.utils import AppSettings

PIPELINE_STAGE_DESCRIPTIONS: dict[PipelineStageName, str] = {
    PipelineStageName.BRIEF_NORMALIZATION: "Normalize raw operator input into a typed brief.",
    PipelineStageName.STRATEGY_GENERATION: "Produce campaign naming, targeting, and keyword themes.",
    PipelineStageName.COPY_GENERATION: "Produce responsive search ad variants for review.",
    PipelineStageName.VALIDATION: "Validate required fields and Google Ads draft constraints.",
    PipelineStageName.GOOGLE_ADS_DRAFT: "Create the paused draft campaign and related assets.",
    PipelineStageName.APPROVAL_REQUEST: "Send the draft summary to the approval channel.",
}


@dataclass(slots=True)
class DemoOrchestrator:
    """Thin orchestration shell for the future end-to-end flow."""

    settings: AppSettings
    brief_parser: BriefParser = field(default_factory=BriefParser)
    strategy_composer: StrategyComposer = field(default_factory=StrategyComposer)
    copy_generator: CopyGenerator = field(default_factory=CopyGenerator)
    google_ads_service: GoogleAdsService = field(default_factory=GoogleAdsService)
    approval_service: ApprovalService = field(default_factory=ApprovalService)
    observability_service: ObservabilityService = field(default_factory=ObservabilityService)

    def preview(self) -> DemoScaffoldSummary:
        stages = [
            StagePreview(name=name, description=description)
            for name, description in PIPELINE_STAGE_DESCRIPTIONS.items()
        ]
        notes = [
            "Business logic is intentionally unimplemented in this scaffold.",
            "Configuration is strict so integration issues fail before runtime work begins.",
            "Google Ads, LangSmith, and n8n service adapters are present as extension points.",
        ]
        return DemoScaffoldSummary(
            environment=self.settings.environment,
            stages=stages,
            notes=notes,
        )

