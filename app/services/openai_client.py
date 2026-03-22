from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.prompts import render_prompt
from app.utils import AppSettings, traceable, wrap_openai_client

LOGGER = logging.getLogger(__name__)
SchemaT = TypeVar("SchemaT", bound=BaseModel)


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
        )

    @traceable(run_type="llm", name="openai_generate_structured")
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

        return responses_api.create(
            model=model,
            input=input_messages,
            text={
                "format": self._build_text_format(response_model),
            },
        )

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
        if LOGGER.isEnabledFor(logging.DEBUG):
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
