from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.campaign import (
    BriefInput,
    CampaignPlan,
    DraftCreationResult,
    ResponsiveSearchAdVariant,
)


class PipelineStageName(str, Enum):
    BRIEF_LOAD = "brief_load"
    BRIEF_NORMALIZATION = "brief_normalization"
    STRATEGY_GENERATION = "strategy_generation"
    COPY_GENERATION = "copy_generation"
    VALIDATION = "validation"
    GOOGLE_ADS_DRAFT = "google_ads_draft"
    APPROVAL_REQUEST = "approval_request"


class PipelineStageStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DemoRunStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StagePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: PipelineStageName
    description: str = Field(min_length=1)
    status: Literal["NOT_STARTED"] = "NOT_STARTED"


class DemoScaffoldSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_name: Literal["rocket-campaign-draft-demo"] = "rocket-campaign-draft-demo"
    environment: str = Field(min_length=1)
    stages: list[StagePreview] = Field(min_length=1)
    notes: list[str] = Field(min_length=1)


class StageExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: PipelineStageName
    description: str = Field(min_length=1)
    status: PipelineStageStatus


class DemoRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_name: Literal["rocket-campaign-draft-demo"] = "rocket-campaign-draft-demo"
    environment: str = Field(min_length=1)
    brief: BriefInput
    campaign_plan: CampaignPlan
    rsa_variants: list[ResponsiveSearchAdVariant] = Field(min_length=1)
    draft_creation_result: DraftCreationResult
    approval_payload: dict[str, Any]
    artifact_path: str = Field(min_length=1)
    stages: list[StageExecutionResult] = Field(min_length=1)


class DemoRunArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_name: Literal["rocket-campaign-draft-demo"] = "rocket-campaign-draft-demo"
    environment: str = Field(min_length=1)
    artifact_path: str = Field(min_length=1)
    run_status: DemoRunStatus
    stages: list[StageExecutionResult] = Field(min_length=1)
    brief: BriefInput | None = None
    campaign_plan: CampaignPlan | None = None
    rsa_variants: list[ResponsiveSearchAdVariant] = Field(default_factory=list)
    draft_creation_result: DraftCreationResult | None = None
    partial_draft_state: dict[str, Any] | None = None
    approval_payload: dict[str, Any] | None = None
    error_message: str | None = None
