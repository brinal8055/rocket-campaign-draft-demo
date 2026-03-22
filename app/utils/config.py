from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.utils.exceptions import ConfigurationError


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
        str_strip_whitespace=True,
    )

    environment: Literal["local", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    openai_api_key: SecretStr
    openai_strategy_model: str = "gpt-5.4"
    openai_transform_model: str = "gpt-5-mini"
    openai_request_timeout_seconds: float = Field(default=60.0, gt=0)
    langsmith_api_key: SecretStr
    langsmith_project: str = "rocket-campaign-draft-demo"
    langsmith_tracing: bool = True
    langsmith_workspace_id: str | None = None
    allow_sensitive_observability: bool = False
    google_ads_developer_token: SecretStr
    google_ads_client_id: str
    google_ads_client_secret: SecretStr
    google_ads_refresh_token: SecretStr
    google_ads_customer_id: str
    google_ads_login_customer_id: str | None = None
    google_ads_use_test_account: bool = True
    n8n_approval_webhook_url: AnyHttpUrl
    n8n_approval_webhook_secret: SecretStr | None = None
    n8n_approval_webhook_secret_header: str = "X-Rocket-Webhook-Secret"

    @field_validator("google_ads_customer_id", "google_ads_login_customer_id")
    @classmethod
    def normalize_customer_id(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None

        digits_only = value.replace("-", "")
        if not digits_only.isdigit():
            raise ValueError("must contain only digits and optional hyphens")
        return digits_only

    @field_validator("n8n_approval_webhook_secret_header")
    @classmethod
    def validate_n8n_secret_header(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value


def load_settings(env_file: str | Path | None = None) -> AppSettings:
    env_path = Path(env_file) if env_file is not None else Path(".env")

    try:
        return AppSettings(_env_file=env_path)
    except ValidationError as exc:
        raise ConfigurationError(_format_validation_errors(exc, env_path)) from exc


def _format_validation_errors(exc: ValidationError, env_path: Path) -> str:
    lines = [
        f"Unable to load settings from '{env_path}'.",
        "Check the values in your environment or copy and fill out .env.example.",
    ]

    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<unknown>"
        error_type = error["type"]

        if error_type == "missing":
            lines.append(f"- Missing required setting: {location}")
        elif error_type == "extra_forbidden":
            lines.append(f"- Unknown setting in dotenv file: {location}")
        else:
            lines.append(f"- Invalid setting '{location}': {error['msg']}")

    return "\n".join(lines)
