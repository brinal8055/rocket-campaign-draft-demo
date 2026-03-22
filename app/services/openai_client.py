from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.prompts import render_prompt
from app.utils import (
    AppSettings,
    sensitive_observability_enabled,
    set_sensitive_observability,
    traceable,
    wrap_openai_client,
)

LOGGER = logging.getLogger(__name__)
SchemaT = TypeVar("SchemaT", bound=BaseModel)


def _sanitize_openai_trace_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    if sensitive_observability_enabled():
        return inputs

    self_obj = inputs.get("self")
    return {
        "model": inputs.get("model"),
        "use_lightweight_model": inputs.get("use_lightweight_model", False),
        "response_model": getattr(inputs.get("response_model"), "__name__", "<unknown>"),
        "system_prompt": "<redacted>",
        "user_prompt": "<redacted>",
        "client_defaults": {
            "strategy_model": getattr(self_obj, "strategy_model", None),
            "transform_model": getattr(self_obj, "transform_model", None),
            "timeout_seconds": getattr(self_obj, "timeout_seconds", None),
        },
    }


def _sanitize_openai_trace_outputs(output: Any) -> Any:
    if sensitive_observability_enabled():
        return output

    if isinstance(output, BaseModel):
        return {
            "model_name": type(output).__name__,
            "field_names": sorted(output.model_dump(mode="python").keys()),
        }
    return {"type": type(output).__name__}


class OpenAIResponseError(RuntimeError):
    """Raised when the OpenAI Responses API cannot produce a usable response."""


@dataclass(slots=True)
class OpenAIResponsesClient:
    api_key: str
    strategy_model: str = "gpt-5.4"
    transform_model: str = "gpt-5-mini"
    timeout_seconds: float = 60.0
    langsmith_api_key: str | None = None
    langsmith_project: str = "rocket-campaign-draft-demo"
    langsmith_tracing: bool = True
    langsmith_workspace_id: str | None = None
    allow_sensitive_observability: bool = False
    sdk_client: Any | None = field(default=None, repr=False)

    @classmethod
    def from_settings(cls, settings: AppSettings) -> OpenAIResponsesClient:
        return cls(
            api_key=settings.openai_api_key.get_secret_value(),
            strategy_model=settings.openai_strategy_model,
            transform_model=settings.openai_transform_model,
            timeout_seconds=settings.openai_request_timeout_seconds,
            langsmith_api_key=settings.langsmith_api_key.get_secret_value(),
            langsmith_project=settings.langsmith_project,
            langsmith_tracing=settings.langsmith_tracing,
            langsmith_workspace_id=settings.langsmith_workspace_id,
            allow_sensitive_observability=settings.allow_sensitive_observability,
        )

    @traceable(
        run_type="llm",
        name="openai_generate_structured",
        process_inputs=_sanitize_openai_trace_inputs,
        process_outputs=_sanitize_openai_trace_outputs,
        exceptions_to_handle=(OpenAIResponseError,),
    )
    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[SchemaT],
        model: str | None = None,
        use_lightweight_model: bool = False,
    ) -> SchemaT:
        selected_model = model or self._select_model(use_lightweight_model)
        input_messages = self._build_input_messages(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        response = self._parse_response(
            model=selected_model,
            input_messages=input_messages,
            response_model=response_model,
        )
        raw_response = self._extract_raw_response(response)
        self._log_raw_response(selected_model, raw_response)

        try:
            return self._coerce_response(response=response, response_model=response_model)
        except ValidationError as exc:
            correction_prompt = render_prompt(
                "structured_retry_correction",
                schema_name=response_model.__name__,
                validation_errors=self._format_validation_error(exc),
                previous_response=raw_response or "<empty response>",
            )
            retry_messages = [
                *input_messages,
                {"role": "user", "content": correction_prompt},
            ]
            retry_response = self._parse_response(
                model=selected_model,
                input_messages=retry_messages,
                response_model=response_model,
            )
            retry_raw_response = self._extract_raw_response(retry_response)
            self._log_raw_response(selected_model, retry_raw_response)

            try:
                return self._coerce_response(
                    response=retry_response,
                    response_model=response_model,
                )
            except ValidationError as retry_exc:
                raise OpenAIResponseError(
                    "OpenAI response failed schema validation after one retry."
                ) from retry_exc

    def _parse_response(
        self,
        *,
        model: str,
        input_messages: list[dict[str, str]],
        response_model: type[SchemaT],
    ) -> Any:
        sdk_client = self._get_sdk_client()
        responses_api = getattr(sdk_client, "responses", None)
        if responses_api is None or not hasattr(responses_api, "create"):
            raise OpenAIResponseError(
                "The installed OpenAI SDK does not support responses.create()."
            )

        try:
            return responses_api.create(
                model=model,
                input=input_messages,
                text={
                    "format": self._build_text_format(response_model),
                },
            )
        except Exception as exc:
            raise OpenAIResponseError(f"OpenAI Responses API request failed: {exc}") from exc

    def _get_sdk_client(self) -> Any:
        if self.sdk_client is not None:
            return self.sdk_client

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise OpenAIResponseError(
                "The 'openai' package is required to use OpenAIResponsesClient."
            ) from exc

        self._configure_langsmith_environment()
        self.sdk_client = wrap_openai_client(
            OpenAI(api_key=self.api_key, timeout=self.timeout_seconds)
        )
        return self.sdk_client

    def _coerce_response(
        self,
        *,
        response: Any,
        response_model: type[SchemaT],
    ) -> SchemaT:
        output_parsed = getattr(response, "output_parsed", None)
        if isinstance(output_parsed, response_model):
            return output_parsed
        if output_parsed is not None:
            return response_model.model_validate(output_parsed)

        raw_response = self._extract_raw_response(response)
        if raw_response:
            return response_model.model_validate_json(raw_response)

        raise OpenAIResponseError("OpenAI response did not include any structured output.")

    def _extract_raw_response(self, response: Any) -> str:
        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        output = getattr(response, "output", None)
        if output is None:
            return ""

        try:
            return json.dumps(output, indent=2, default=str)
        except TypeError:
            return str(output)

    def _log_raw_response(self, model: str, raw_response: str) -> None:
        if LOGGER.isEnabledFor(logging.DEBUG) and self.allow_sensitive_observability:
            LOGGER.debug("Raw model response for %s:%s%s", model, "\n", raw_response or "<empty>")

    def _select_model(self, use_lightweight_model: bool) -> str:
        if use_lightweight_model:
            return self.transform_model
        return self.strategy_model

    def _build_input_messages(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _format_validation_error(self, exc: ValidationError) -> str:
        return json.dumps(exc.errors(), indent=2)

    def _configure_langsmith_environment(self) -> None:
        set_sensitive_observability(self.allow_sensitive_observability)
        if not self.langsmith_tracing or not self.langsmith_api_key or not self.langsmith_project:
            return

        import os

        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = self.langsmith_api_key
        os.environ["LANGSMITH_PROJECT"] = self.langsmith_project
        if self.langsmith_workspace_id:
            os.environ["LANGSMITH_WORKSPACE_ID"] = self.langsmith_workspace_id

    def _build_text_format(self, response_model: type[SchemaT]) -> dict[str, Any]:
        schema = self._build_strict_json_schema(response_model)
        return {
            "type": "json_schema",
            "name": response_model.__name__,
            "strict": True,
            "schema": self._strip_unsupported_json_schema_keywords(schema),
        }

    def _build_strict_json_schema(self, response_model: type[SchemaT]) -> dict[str, Any]:
        try:
            from openai.lib._pydantic import to_strict_json_schema
        except ImportError:
            return response_model.model_json_schema()

        return to_strict_json_schema(response_model)

    def _strip_unsupported_json_schema_keywords(self, schema: Any) -> Any:
        if isinstance(schema, dict):
            return {
                key: self._strip_unsupported_json_schema_keywords(value)
                for key, value in schema.items()
                if key != "format"
            }

        if isinstance(schema, list):
            return [self._strip_unsupported_json_schema_keywords(item) for item in schema]

        return schema
