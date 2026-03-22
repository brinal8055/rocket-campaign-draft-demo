from __future__ import annotations

import json
import logging
import socket
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.schemas import ResponsiveSearchAdVariant
from app.utils import AppSettings, mask_identifier, sensitive_observability_enabled, traceable

LOGGER = logging.getLogger(__name__)

TRANSIENT_HTTP_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


def _sanitize_n8n_trace_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    if sensitive_observability_enabled():
        return inputs

    ad_variants = inputs.get("ad_variants") or []
    return {
        "campaign_name": inputs.get("campaign_name"),
        "customer_id": mask_identifier(str(inputs.get("customer_id", ""))),
        "campaign_status": inputs.get("campaign_status"),
        "daily_budget": inputs.get("daily_budget"),
        "daily_budget_currency": inputs.get("daily_budget_currency"),
        "keyword_theme_count": len(inputs.get("keyword_themes") or []),
        "ad_variant_count": len(ad_variants),
        "landing_page_url": "<redacted>",
    }


def _sanitize_n8n_trace_outputs(output: Any) -> Any:
    if sensitive_observability_enabled():
        return output

    if isinstance(output, dict):
        return {
            "campaign_name": output.get("campaign_name"),
            "customer_id": mask_identifier(str(output.get("customer_id", ""))),
            "campaign_status": output.get("campaign_status"),
            "daily_budget": output.get("daily_budget"),
            "daily_budget_currency": output.get("daily_budget_currency"),
        }
    return output


class RequestSender(Protocol):
    def __call__(self, request: Request, *, timeout: float) -> Any: ...


class N8NServiceError(RuntimeError):
    """Raised when the n8n approval webhook cannot be reached successfully."""


@dataclass(slots=True)
class N8NService:
    """Send approval requests to an n8n webhook."""

    webhook_url: str | None = None
    webhook_secret: str | None = None
    webhook_secret_header: str = "X-Rocket-Webhook-Secret"
    timeout_seconds: float = 10.0
    max_attempts: int = 2
    request_sender: RequestSender = field(default=urlopen, repr=False)

    @classmethod
    def from_settings(cls, settings: AppSettings) -> N8NService:
        return cls(
            webhook_url=str(settings.n8n_approval_webhook_url),
            webhook_secret=(
                settings.n8n_approval_webhook_secret.get_secret_value()
                if settings.n8n_approval_webhook_secret is not None
                else None
            ),
            webhook_secret_header=settings.n8n_approval_webhook_secret_header,
        )

    @traceable(
        run_type="tool",
        name="n8n_approval_request",
        process_inputs=_sanitize_n8n_trace_inputs,
        process_outputs=_sanitize_n8n_trace_outputs,
    )
    def send_campaign_draft_for_approval(
        self,
        *,
        campaign_name: str,
        customer_id: str,
        campaign_resource_name: str,
        campaign_status: str,
        landing_page_url: str,
        daily_budget: float,
        daily_budget_currency: str,
        keyword_themes: list[str],
        ad_variants: list[ResponsiveSearchAdVariant],
    ) -> dict[str, Any]:
        payload = self.build_campaign_draft_payload(
            campaign_name=campaign_name,
            customer_id=customer_id,
            campaign_resource_name=campaign_resource_name,
            campaign_status=campaign_status,
            landing_page_url=landing_page_url,
            daily_budget=daily_budget,
            daily_budget_currency=daily_budget_currency,
            keyword_themes=keyword_themes,
            ad_variants=ad_variants,
        )
        self.request_approval(payload)
        return payload

    def build_campaign_draft_payload(
        self,
        *,
        campaign_name: str,
        customer_id: str,
        campaign_resource_name: str,
        campaign_status: str,
        landing_page_url: str,
        daily_budget: float,
        daily_budget_currency: str,
        keyword_themes: list[str],
        ad_variants: list[ResponsiveSearchAdVariant],
    ) -> dict[str, Any]:
        if not ad_variants:
            raise ValueError("At least one responsive search ad variant is required.")
        if not keyword_themes:
            raise ValueError("At least one keyword theme is required.")

        return {
            "campaign_name": campaign_name,
            "customer_id": customer_id,
            "campaign_resource_name": campaign_resource_name,
            "campaign_status": campaign_status,
            "landing_page_url": landing_page_url,
            "daily_budget": daily_budget,
            "daily_budget_currency": daily_budget_currency,
            "keyword_themes": keyword_themes,
            "ad_copy_summary": self._build_ad_copy_summary(ad_variants),
        }

    def request_approval(self, payload: dict[str, Any]) -> None:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.webhook_secret:
            headers[self.webhook_secret_header] = self.webhook_secret

        request = Request(
            url=self._require_webhook_url(),
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.request_sender(request, timeout=self.timeout_seconds)
                self._validate_response(response)
                if sensitive_observability_enabled():
                    LOGGER.info("Sent approval request to n8n for campaign '%s'.", payload["campaign_name"])
                else:
                    LOGGER.info("Sent approval request to n8n.")
                return
            except HTTPError as exc:
                if self._is_transient_status(exc.code) and attempt < self.max_attempts:
                    LOGGER.warning(
                        "Transient n8n webhook failure (HTTP %s). Retrying once.",
                        exc.code,
                    )
                    continue
                raise N8NServiceError(self._format_http_error(exc)) from exc
            except (URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
                if attempt < self.max_attempts:
                    LOGGER.warning("n8n webhook was temporarily unreachable. Retrying once.")
                    continue
                raise N8NServiceError(
                    "n8n approval webhook is unreachable. Check N8N_APPROVAL_WEBHOOK_URL and network access."
                ) from exc

    def _validate_response(self, response: Any) -> None:
        status = getattr(response, "status", None)
        if status is None:
            status = getattr(response, "code", None)

        if status is None or 200 <= int(status) < 300:
            return

        raise N8NServiceError(
            f"n8n approval webhook returned an unexpected HTTP {status} response."
        )

    def _build_ad_copy_summary(
        self,
        ad_variants: list[ResponsiveSearchAdVariant],
    ) -> str:
        summary_chunks = []
        for index, ad_variant in enumerate(ad_variants[:3], start=1):
            headlines = " / ".join(ad_variant.headlines[:3])
            descriptions = " / ".join(ad_variant.descriptions[:2])
            summary_chunks.append(f"V{index}: H={headlines}; D={descriptions}")
        return " || ".join(summary_chunks)

    def _require_webhook_url(self) -> str:
        if not self.webhook_url:
            raise N8NServiceError("N8N approval webhook URL is not configured.")
        return self.webhook_url

    def _format_http_error(self, exc: HTTPError) -> str:
        if self._is_transient_status(exc.code):
            return (
                f"n8n approval webhook failed after retry with HTTP {exc.code}. "
                "Check whether the webhook endpoint is healthy."
            )
        return f"n8n approval webhook rejected the request with HTTP {exc.code}."

    def _is_transient_status(self, status_code: int) -> bool:
        return status_code in TRANSIENT_HTTP_STATUS_CODES
