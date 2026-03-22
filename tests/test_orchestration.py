from __future__ import annotations

from pydantic import SecretStr

from app.orchestration import DemoOrchestrator
from app.schemas import PipelineStageName
from app.utils import AppSettings


def build_settings() -> AppSettings:
    return AppSettings(
        environment="local",
        log_level="INFO",
        openai_api_key=SecretStr("test-openai-key"),
        langsmith_api_key=SecretStr("test-langsmith-key"),
        langsmith_project="rocket-demo-tests",
        google_ads_developer_token=SecretStr("dev-token"),
        google_ads_client_id="client-id",
        google_ads_client_secret=SecretStr("client-secret"),
        google_ads_refresh_token=SecretStr("refresh-token"),
        google_ads_customer_id="1234567890",
        google_ads_use_test_account=True,
        n8n_approval_webhook_url="https://example.com/webhook/approval",
    )


def test_preview_contains_expected_pipeline_order() -> None:
    summary = DemoOrchestrator(settings=build_settings()).preview()

    assert [stage.name for stage in summary.stages] == [
        PipelineStageName.BRIEF_NORMALIZATION,
        PipelineStageName.STRATEGY_GENERATION,
        PipelineStageName.COPY_GENERATION,
        PipelineStageName.VALIDATION,
        PipelineStageName.GOOGLE_ADS_DRAFT,
        PipelineStageName.APPROVAL_REQUEST,
    ]
    assert summary.environment == "local"
    assert len(summary.notes) >= 1

