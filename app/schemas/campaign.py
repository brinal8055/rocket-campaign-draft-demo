from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl, field_validator


def _validate_non_empty_string_list(
    values: list[str],
    *,
    field_name: str,
    min_items: int = 1,
) -> list[str]:
    normalized_values = [value.strip() for value in values]

    if len(normalized_values) < min_items:
        item_label = "item" if min_items == 1 else "items"
        raise ValueError(f"{field_name} must contain at least {min_items} {item_label}.")

    if any(not value for value in normalized_values):
        raise ValueError(f"{field_name} must not contain empty values.")

    return normalized_values


def _validate_positive_budget(value: float, *, field_name: str) -> float:
    if value <= 0:
        raise ValueError(f"{field_name} must be positive.")
    return value


def _validate_currency_code(value: str, *, field_name: str) -> str:
    normalized_value = value.strip().upper()
    if len(normalized_value) != 3 or not normalized_value.isalpha():
        raise ValueError(f"{field_name} must be a 3-letter ISO currency code.")
    return normalized_value


def _validate_max_string_length(
    values: list[str],
    *,
    field_name: str,
    max_length: int,
) -> list[str]:
    too_long_values = [value for value in values if len(value) > max_length]
    if too_long_values:
        raise ValueError(
            f"Each {field_name[:-1]} must be at most {max_length} characters."
        )
    return values


class BriefInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_name: str = Field(min_length=1)
    offer: str = Field(min_length=1)
    goal: Literal["lead_gen", "demo_bookings", "purchases"]
    audience: str = Field(min_length=1)
    geo: list[str]
    daily_budget_amount: float = Field(
        validation_alias=AliasChoices(
            "daily_budget_amount",
            "daily_budget_usd",
            "daily_budget",
        )
    )
    budget_currency_code: str = Field(min_length=3, max_length=3)
    landing_page_url: HttpUrl
    tone: str = Field(min_length=1)
    brand_notes: str = Field(min_length=1)

    @field_validator("geo")
    @classmethod
    def validate_geo(cls, value: list[str]) -> list[str]:
        return _validate_non_empty_string_list(value, field_name="geo")

    @field_validator("daily_budget_amount")
    @classmethod
    def validate_daily_budget_amount(cls, value: float) -> float:
        return _validate_positive_budget(value, field_name="daily_budget_amount")

    @field_validator("budget_currency_code")
    @classmethod
    def validate_budget_currency_code(cls, value: str) -> str:
        return _validate_currency_code(value, field_name="budget_currency_code")


class CampaignPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    campaign_name: str = Field(min_length=1)
    channel: Literal["google_search"]
    keyword_themes: list[str] = Field(min_length=1)
    messaging_angles: list[str] = Field(min_length=1)
    utm_source: Literal["google"] = "google"
    utm_medium: Literal["cpc"] = "cpc"
    utm_campaign: str = Field(min_length=1)
    geo_targets: list[str]
    recommended_daily_budget_amount: float = Field(
        validation_alias=AliasChoices(
            "recommended_daily_budget_amount",
            "recommended_daily_budget_usd",
        )
    )
    budget_currency_code: str = Field(min_length=3, max_length=3)

    @field_validator("keyword_themes", "messaging_angles")
    @classmethod
    def validate_non_empty_list_fields(cls, value: list[str], info) -> list[str]:
        return _validate_non_empty_string_list(value, field_name=info.field_name)

    @field_validator("geo_targets")
    @classmethod
    def validate_geo_targets(cls, value: list[str]) -> list[str]:
        return _validate_non_empty_string_list(value, field_name="geo_targets")

    @field_validator("recommended_daily_budget_amount")
    @classmethod
    def validate_recommended_daily_budget_amount(cls, value: float) -> float:
        return _validate_positive_budget(value, field_name="recommended_daily_budget_amount")

    @field_validator("budget_currency_code")
    @classmethod
    def validate_plan_budget_currency_code(cls, value: str) -> str:
        return _validate_currency_code(value, field_name="budget_currency_code")


class ResponsiveSearchAdVariant(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    headlines: list[str]
    descriptions: list[str]
    final_url: HttpUrl
    path1: str = Field(min_length=1)
    path2: str = Field(min_length=1)

    @field_validator("headlines")
    @classmethod
    def validate_headlines(cls, value: list[str]) -> list[str]:
        normalized_values = _validate_non_empty_string_list(
            value,
            field_name="headlines",
            min_items=3,
        )
        return _validate_max_string_length(
            normalized_values,
            field_name="headlines",
            max_length=30,
        )

    @field_validator("descriptions")
    @classmethod
    def validate_descriptions(cls, value: list[str]) -> list[str]:
        normalized_values = _validate_non_empty_string_list(
            value,
            field_name="descriptions",
            min_items=2,
        )
        return _validate_max_string_length(
            normalized_values,
            field_name="descriptions",
            max_length=90,
        )

    @field_validator("final_url", mode="before")
    @classmethod
    def validate_final_url_present(cls, value: object) -> object:
        if value is None:
            raise ValueError("final_url must be present.")
        if isinstance(value, str) and not value.strip():
            raise ValueError("final_url must be present.")
        return value

    @field_validator("path1", "path2")
    @classmethod
    def validate_display_path_length(cls, value: str) -> str:
        if len(value) > 15:
            raise ValueError("display paths must be at most 15 characters.")
        return value


class DraftCreationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    campaign_resource_name: str = Field(min_length=1)
    campaign_status: Literal["PAUSED"]
    ad_group_resource_name: str = Field(min_length=1)
    campaign_budget_resource_name: str | None = Field(default=None, min_length=1)
    ad_group_ad_resource_name: str | None = Field(default=None, min_length=1)
    keyword_count: int = Field(ge=0)
    geo_target_count: int = Field(ge=0)
    approval_status: Literal["PENDING"]
    keyword_resource_names: list[str] = Field(default_factory=list)
    geo_target_resource_names: list[str] = Field(default_factory=list)
    account_currency_code: str | None = Field(default=None, min_length=3, max_length=3)

    @field_validator("account_currency_code")
    @classmethod
    def validate_account_currency_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_currency_code(value, field_name="account_currency_code")
