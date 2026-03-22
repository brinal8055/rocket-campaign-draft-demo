from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.schemas import CampaignPlan, DraftCreationResult, ResponsiveSearchAdVariant
from app.utils import AppSettings, traceable

LOGGER = logging.getLogger(__name__)

ACCOUNT_METADATA_QUERY = """
SELECT
  customer.id,
  customer.descriptive_name,
  customer.currency_code,
  customer.time_zone,
  customer.manager,
  customer.test_account
FROM customer
LIMIT 1
""".strip()

COMMON_GEO_ALIASES = {
    "UK": "United Kingdom",
    "UAE": "United Arab Emirates",
    "US": "United States",
    "USA": "United States",
}


class GoogleAdsServiceError(RuntimeError):
    """Raised when Google Ads configuration or API execution fails."""


@dataclass(slots=True)
class GoogleAdsService:
    """Adapter for Google Ads authentication and paused draft creation."""

    customer_id: str | None = None
    developer_token: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    refresh_token: str | None = None
    login_customer_id: str | None = None
    use_proto_plus: bool = True
    google_ads_client: Any | None = field(default=None, repr=False)

    @classmethod
    def from_settings(cls, settings: AppSettings) -> GoogleAdsService:
        return cls(
            customer_id=settings.google_ads_customer_id,
            developer_token=settings.google_ads_developer_token.get_secret_value(),
            client_id=settings.google_ads_client_id,
            client_secret=settings.google_ads_client_secret.get_secret_value(),
            refresh_token=settings.google_ads_refresh_token.get_secret_value(),
            login_customer_id=settings.google_ads_login_customer_id,
        )

    @traceable(run_type="tool", name="google_ads_create_paused_draft")
    def create_paused_draft(
        self,
        campaign_plan: CampaignPlan,
        ad_variants: list[ResponsiveSearchAdVariant],
    ) -> DraftCreationResult:
        if not ad_variants:
            raise ValueError("At least one responsive search ad variant is required.")

        client = self._get_client()
        google_ads_exception_class = self._google_ads_exception_class()
        try:
            campaign_budget_resource_name = self._create_campaign_budget(
                client=client,
                campaign_plan=campaign_plan,
            )
            campaign_resource_name = self._create_search_campaign(
                client=client,
                campaign_plan=campaign_plan,
                campaign_budget_resource_name=campaign_budget_resource_name,
            )
            ad_group_resource_name = self._create_ad_group(
                client=client,
                campaign_plan=campaign_plan,
                campaign_resource_name=campaign_resource_name,
            )
            keyword_resource_names = self._add_keywords(
                client=client,
                ad_group_resource_name=ad_group_resource_name,
                keywords=campaign_plan.keyword_themes,
            )
            geo_target_resource_names = self._add_geo_targeting(
                client=client,
                campaign_resource_name=campaign_resource_name,
                geo_targets=campaign_plan.geo_targets,
            )
            self._create_responsive_search_ad(
                client=client,
                ad_group_resource_name=ad_group_resource_name,
                ad_variant=ad_variants[0],
            )
        except Exception as exc:
            if google_ads_exception_class is None or not isinstance(exc, google_ads_exception_class):
                raise
            raise GoogleAdsServiceError(self._format_google_ads_exception(exc)) from exc

        return DraftCreationResult(
            campaign_resource_name=campaign_resource_name,
            campaign_status="PAUSED",
            ad_group_resource_name=ad_group_resource_name,
            keyword_count=len(keyword_resource_names),
            geo_target_count=len(geo_target_resource_names),
            approval_status="PENDING",
        )

    def get_account_metadata(self) -> dict[str, object]:
        client = self._get_client()
        google_ads_service = client.get_service("GoogleAdsService")
        google_ads_exception_class = self._google_ads_exception_class()

        try:
            response = google_ads_service.search(
                customer_id=self._require_customer_id(),
                query=ACCOUNT_METADATA_QUERY,
            )
        except Exception as exc:
            if google_ads_exception_class is None or not isinstance(exc, google_ads_exception_class):
                raise
            raise GoogleAdsServiceError(self._format_google_ads_exception(exc)) from exc

        first_row = next(iter(response), None)
        if first_row is None:
            raise GoogleAdsServiceError("No account metadata was returned for the configured customer.")

        customer = first_row.customer
        return {
            "customer_id": str(customer.id),
            "descriptive_name": customer.descriptive_name,
            "currency_code": customer.currency_code,
            "time_zone": customer.time_zone,
            "is_manager": bool(customer.manager),
            "is_test_account": bool(getattr(customer, "test_account", False)),
            "login_customer_id": self.login_customer_id,
        }

    def validate_auth(self) -> dict[str, object]:
        return self.get_account_metadata()

    def _get_client(self) -> Any:
        if self.google_ads_client is not None:
            return self.google_ads_client

        missing = [
            name
            for name, value in (
                ("customer_id", self.customer_id),
                ("developer_token", self.developer_token),
                ("client_id", self.client_id),
                ("client_secret", self.client_secret),
                ("refresh_token", self.refresh_token),
            )
            if not value
        ]
        if missing:
            raise GoogleAdsServiceError(
                f"Google Ads credentials are incomplete. Missing: {', '.join(missing)}."
            )

        try:
            from google.ads.googleads.client import GoogleAdsClient
        except ImportError as exc:
            raise GoogleAdsServiceError(
                "The 'google-ads' package is required to use GoogleAdsService."
            ) from exc

        self.google_ads_client = GoogleAdsClient.load_from_dict(
            self._build_client_configuration()
        )
        return self.google_ads_client

    def _build_client_configuration(self) -> dict[str, object]:
        configuration: dict[str, object] = {
            "developer_token": self.developer_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "use_proto_plus": self.use_proto_plus,
        }
        if self.login_customer_id:
            configuration["login_customer_id"] = self.login_customer_id
        return configuration

    def _create_campaign_budget(
        self,
        *,
        client: Any,
        campaign_plan: CampaignPlan,
    ) -> str:
        campaign_budget_service = client.get_service("CampaignBudgetService")
        operation = client.get_type("CampaignBudgetOperation")
        campaign_budget = operation.create
        campaign_budget.name = self._build_unique_name(campaign_plan.campaign_name, "Budget")
        campaign_budget.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
        campaign_budget.amount_micros = self._usd_to_micros(
            campaign_plan.recommended_daily_budget_usd
        )

        response = campaign_budget_service.mutate_campaign_budgets(
            customer_id=self._require_customer_id(),
            operations=[operation],
        )
        resource_name = response.results[0].resource_name
        LOGGER.info("Created campaign budget %s", resource_name)
        return resource_name

    @traceable(run_type="tool", name="google_ads_create_campaign")
    def _create_search_campaign(
        self,
        *,
        client: Any,
        campaign_plan: CampaignPlan,
        campaign_budget_resource_name: str,
    ) -> str:
        campaign_service = client.get_service("CampaignService")
        operation = client.get_type("CampaignOperation")
        campaign = operation.create
        campaign.name = self._build_unique_name(campaign_plan.campaign_name, "Campaign")
        campaign.advertising_channel_type = client.enums.AdvertisingChannelTypeEnum.SEARCH
        campaign.status = client.enums.CampaignStatusEnum.PAUSED
        campaign.manual_cpc = client.get_type("ManualCpc")
        campaign.campaign_budget = campaign_budget_resource_name
        campaign.network_settings.target_google_search = True
        campaign.network_settings.target_search_network = True
        campaign.network_settings.target_partner_search_network = False
        campaign.network_settings.target_content_network = True
        campaign.contains_eu_political_advertising = (
            client.enums.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
        )

        start_date = dt.date.today() + dt.timedelta(days=1)
        end_date = start_date + dt.timedelta(weeks=4)
        campaign.start_date_time = f"{start_date:%Y%m%d} 00:00:00"
        campaign.end_date_time = f"{end_date:%Y%m%d} 23:59:59"

        response = campaign_service.mutate_campaigns(
            customer_id=self._require_customer_id(),
            operations=[operation],
        )
        resource_name = response.results[0].resource_name
        LOGGER.info("Created paused search campaign %s", resource_name)
        return resource_name

    @traceable(run_type="tool", name="google_ads_create_ad_group")
    def _create_ad_group(
        self,
        *,
        client: Any,
        campaign_plan: CampaignPlan,
        campaign_resource_name: str,
    ) -> str:
        ad_group_service = client.get_service("AdGroupService")
        operation = client.get_type("AdGroupOperation")
        ad_group = operation.create
        ad_group.name = self._build_unique_name(campaign_plan.campaign_name, "Ad Group")
        ad_group.status = client.enums.AdGroupStatusEnum.ENABLED
        ad_group.campaign = campaign_resource_name
        ad_group.type_ = client.enums.AdGroupTypeEnum.SEARCH_STANDARD

        response = ad_group_service.mutate_ad_groups(
            customer_id=self._require_customer_id(),
            operations=[operation],
        )
        resource_name = response.results[0].resource_name
        LOGGER.info("Created ad group %s", resource_name)
        return resource_name

    @traceable(run_type="tool", name="google_ads_add_keywords")
    def _add_keywords(
        self,
        *,
        client: Any,
        ad_group_resource_name: str,
        keywords: list[str],
    ) -> list[str]:
        ad_group_criterion_service = client.get_service("AdGroupCriterionService")
        operations = []

        for keyword_text in self._normalize_keywords(keywords):
            operation = client.get_type("AdGroupCriterionOperation")
            ad_group_criterion = operation.create
            ad_group_criterion.ad_group = ad_group_resource_name
            ad_group_criterion.status = client.enums.AdGroupCriterionStatusEnum.ENABLED
            ad_group_criterion.keyword.text = keyword_text
            ad_group_criterion.keyword.match_type = client.enums.KeywordMatchTypeEnum.BROAD
            operations.append(operation)

        response = ad_group_criterion_service.mutate_ad_group_criteria(
            customer_id=self._require_customer_id(),
            operations=operations,
        )
        resource_names = [result.resource_name for result in response.results]
        LOGGER.info("Created %s keywords.", len(resource_names))
        return resource_names

    @traceable(run_type="tool", name="google_ads_add_geo_targeting")
    def _add_geo_targeting(
        self,
        *,
        client: Any,
        campaign_resource_name: str,
        geo_targets: list[str],
    ) -> list[str]:
        campaign_criterion_service = client.get_service("CampaignCriterionService")
        geo_target_constants = self._resolve_geo_target_constants(
            client=client,
            geo_targets=geo_targets,
        )
        operations = []

        for geo_target_constant in geo_target_constants:
            operation = client.get_type("CampaignCriterionOperation")
            campaign_criterion = operation.create
            campaign_criterion.campaign = campaign_resource_name
            campaign_criterion.location.geo_target_constant = geo_target_constant
            operations.append(operation)

        response = campaign_criterion_service.mutate_campaign_criteria(
            customer_id=self._require_customer_id(),
            operations=operations,
        )
        resource_names = [result.resource_name for result in response.results]
        LOGGER.info("Created %s geo targets.", len(resource_names))
        return resource_names

    @traceable(run_type="tool", name="google_ads_create_responsive_search_ad")
    def _create_responsive_search_ad(
        self,
        *,
        client: Any,
        ad_group_resource_name: str,
        ad_variant: ResponsiveSearchAdVariant,
    ) -> str:
        ad_group_ad_service = client.get_service("AdGroupAdService")
        operation = client.get_type("AdGroupAdOperation")
        ad_group_ad = operation.create
        ad_group_ad.status = client.enums.AdGroupAdStatusEnum.ENABLED
        ad_group_ad.ad_group = ad_group_resource_name
        ad_group_ad.ad.final_urls.append(str(ad_variant.final_url))
        ad_group_ad.ad.responsive_search_ad.path1 = ad_variant.path1
        ad_group_ad.ad.responsive_search_ad.path2 = ad_variant.path2
        ad_group_ad.ad.responsive_search_ad.headlines.extend(
            [self._create_ad_text_asset(client, text) for text in ad_variant.headlines]
        )
        ad_group_ad.ad.responsive_search_ad.descriptions.extend(
            [self._create_ad_text_asset(client, text) for text in ad_variant.descriptions]
        )

        response = ad_group_ad_service.mutate_ad_group_ads(
            customer_id=self._require_customer_id(),
            operations=[operation],
        )
        resource_name = response.results[0].resource_name
        LOGGER.info("Created responsive search ad %s", resource_name)
        return resource_name

    def _resolve_geo_target_constants(
        self,
        *,
        client: Any,
        geo_targets: list[str],
    ) -> list[str]:
        geo_target_constant_service = client.get_service("GeoTargetConstantService")
        resolved_resource_names: list[str] = []

        for geo_target in dict.fromkeys(value.strip() for value in geo_targets):
            normalized_geo_target = self._normalize_geo_target(geo_target)
            if normalized_geo_target.startswith("geoTargetConstants/"):
                resolved_resource_names.append(normalized_geo_target)
                continue
            if normalized_geo_target.isdigit():
                resolved_resource_names.append(
                    geo_target_constant_service.geo_target_constant_path(
                        normalized_geo_target
                    )
                )
                continue

            request = client.get_type("SuggestGeoTargetConstantsRequest")
            request.locale = "en"
            request.location_names.names.append(normalized_geo_target)

            suggestions_response = geo_target_constant_service.suggest_geo_target_constants(
                request
            )
            suggestions = list(suggestions_response.geo_target_constant_suggestions)
            if not suggestions:
                raise GoogleAdsServiceError(
                    f"Could not resolve geo target '{geo_target}' to a Google Ads location."
                )

            selected_suggestion = self._select_geo_target_suggestion(
                suggestions=suggestions,
                requested_geo_target=normalized_geo_target,
            )
            resolved_resource_names.append(
                selected_suggestion.geo_target_constant.resource_name
            )

        return resolved_resource_names

    def _select_geo_target_suggestion(
        self,
        *,
        suggestions: list[Any],
        requested_geo_target: str,
    ) -> Any:
        requested_lower = requested_geo_target.casefold()
        for suggestion in suggestions:
            constant = suggestion.geo_target_constant
            if getattr(constant, "name", "").casefold() == requested_lower:
                return suggestion
        return suggestions[0]

    def _create_ad_text_asset(self, client: Any, text: str) -> Any:
        ad_text_asset = client.get_type("AdTextAsset")
        ad_text_asset.text = text
        return ad_text_asset

    def _normalize_keywords(self, keywords: list[str]) -> list[str]:
        normalized_keywords = list(dict.fromkeys(keyword.strip() for keyword in keywords))
        filtered_keywords = [keyword for keyword in normalized_keywords if keyword]
        if not filtered_keywords:
            raise GoogleAdsServiceError("At least one non-empty keyword is required.")
        return filtered_keywords

    def _normalize_geo_target(self, geo_target: str) -> str:
        stripped = geo_target.strip()
        return COMMON_GEO_ALIASES.get(stripped.upper(), stripped)

    def _build_unique_name(self, base_name: str, suffix: str) -> str:
        compact_base_name = " ".join(base_name.split())
        unique_suffix = f"{suffix} {uuid.uuid4().hex[:8]}"
        max_base_length = max(1, 255 - len(unique_suffix) - 1)
        return f"{compact_base_name[:max_base_length]} {unique_suffix}"

    def _usd_to_micros(self, usd_amount: float) -> int:
        return int(round(usd_amount * 1_000_000))

    def _require_customer_id(self) -> str:
        if not self.customer_id:
            raise GoogleAdsServiceError("Google Ads customer_id is not configured.")
        return self.customer_id

    def _google_ads_exception_class(self) -> type[Exception] | None:
        try:
            from google.ads.googleads.errors import GoogleAdsException
        except ImportError:
            return None
        return GoogleAdsException

    def _format_google_ads_exception(self, exc: Exception) -> str:
        request_id = getattr(exc, "request_id", None)
        failure = getattr(exc, "failure", None)
        message_lines = [
            "Google Ads API request failed.",
        ]
        if request_id:
            message_lines.append(f"Request ID: {request_id}")
        if failure and getattr(failure, "errors", None):
            for error in failure.errors:
                message_lines.append(f"- {error.message}")
        elif str(exc):
            message_lines.append(str(exc))
        return "\n".join(message_lines)
