from __future__ import annotations

from pathlib import Path

import pytest

from app.utils import AppSettings, ConfigurationError, load_settings

REQUIRED_ENV_VARS = [
    "OPENAI_API_KEY",
    "LANGSMITH_API_KEY",
    "GOOGLE_ADS_DEVELOPER_TOKEN",
    "GOOGLE_ADS_CLIENT_ID",
    "GOOGLE_ADS_CLIENT_SECRET",
    "GOOGLE_ADS_REFRESH_TOKEN",
    "GOOGLE_ADS_CUSTOMER_ID",
    "N8N_APPROVAL_WEBHOOK_URL",
]


def clear_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in REQUIRED_ENV_VARS:
        monkeypatch.delenv(key, raising=False)


def write_env_file(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_load_settings_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_required_env(monkeypatch)
    env_file = write_env_file(
        tmp_path / ".env",
        "\n".join(
            [
                "ENVIRONMENT=local",
                "LOG_LEVEL=INFO",
                "OPENAI_API_KEY=test-openai-key",
                "LANGSMITH_API_KEY=test-langsmith-key",
                "LANGSMITH_PROJECT=rocket-demo-tests",
                "GOOGLE_ADS_DEVELOPER_TOKEN=dev-token",
                "GOOGLE_ADS_CLIENT_ID=client-id",
                "GOOGLE_ADS_CLIENT_SECRET=client-secret",
                "GOOGLE_ADS_REFRESH_TOKEN=refresh-token",
                "GOOGLE_ADS_CUSTOMER_ID=123-456-7890",
                "GOOGLE_ADS_USE_TEST_ACCOUNT=true",
                "N8N_APPROVAL_WEBHOOK_URL=https://example.com/webhook/approval",
            ]
        ),
    )

    settings = load_settings(env_file)

    assert isinstance(settings, AppSettings)
    assert settings.google_ads_customer_id == "1234567890"
    assert settings.google_ads_use_test_account is True


def test_load_settings_missing_required_values_has_helpful_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_required_env(monkeypatch)
    env_file = write_env_file(tmp_path / ".env", "ENVIRONMENT=local\n")

    with pytest.raises(ConfigurationError) as exc_info:
        load_settings(env_file)

    message = str(exc_info.value)
    assert "Unable to load settings" in message
    assert "Missing required setting: openai_api_key" in message
    assert "Missing required setting: google_ads_customer_id" in message


def test_load_settings_rejects_unknown_dotenv_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_required_env(monkeypatch)
    env_file = write_env_file(
        tmp_path / ".env",
        "\n".join(
            [
                "OPENAI_API_KEY=test-openai-key",
                "LANGSMITH_API_KEY=test-langsmith-key",
                "GOOGLE_ADS_DEVELOPER_TOKEN=dev-token",
                "GOOGLE_ADS_CLIENT_ID=client-id",
                "GOOGLE_ADS_CLIENT_SECRET=client-secret",
                "GOOGLE_ADS_REFRESH_TOKEN=refresh-token",
                "GOOGLE_ADS_CUSTOMER_ID=1234567890",
                "N8N_APPROVAL_WEBHOOK_URL=https://example.com/webhook/approval",
                "UNEXPECTED_SETTING=value",
            ]
        ),
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_settings(env_file)

    assert "Unknown setting in dotenv file: unexpected_setting" in str(exc_info.value)

