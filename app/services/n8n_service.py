from __future__ import annotations

import json
import logging
import socket
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.schemas import ResponsiveSearchAdVariant
from app.utils import AppSettings, traceable

LOGGER = logging.getLogger(__name__)

TRANSIENT_HTTP_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class RequestSender(Protocol):
    def __call__(self, request: Request, *, timeout: float) -> Any: ...


class N8NServiceError(RuntimeError):
    """Raised when the n8n approval webhook cannot be reached successfully."""


@dataclass(slots=True)
class N8NService:
    """Send approval requests to an n8n webhook."""

    webhook_url: str | None = None
    timeout_seconds: float = 10.0
    max_attempts: int = 2
    request_sender: RequestSender = field(default=urlopen, repr=False)

    @classmethod
    def from_settings(cls, settings: AppSettings) -> N8NService:
        return cls(webhook_url=str(settings.n8n_approval_webhook_url))

    @traceable(run_type="tool", name="n8n_approval_request")
    def send_campaign_draft_for_approval(
        self,
        *,
        campaign_name: str,
        customer_id: str,
        campaign_resource_name: str,
        campaign_status: str,
        landing_page_url: str,
        daily_budget: float,
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
            "keyword_themes": keyword_themes,
            "ad_copy_summary": self._build_ad_copy_summary(ad_variants),
        }

    def request_approval(self, payload: dict[str, Any]) -> None:
        request = Request(
            url=self._require_webhook_url(),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.request_sender(request, timeout=self.timeout_seconds)
                self._validate_response(response)
                LOGGER.info("Sent approval request to n8n for campaign '%s'.", payload["campaign_name"])
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
