from __future__ import annotations

import logging

import pytest
from pydantic import BaseModel, Field

from app.agents import BriefParser, RSACopyGenerator, StrategyComposer
from app.agents.rsa_copy_generator import RSACopyGenerationResult
from app.schemas import BriefInput, CampaignPlan
from app.services.openai_client import OpenAIResponsesClient, OpenAIResponseError


class ExampleResponse(BaseModel):
    value: str = Field(min_length=1)


class PositiveIntegerResponse(BaseModel):
    count: int = Field(gt=0)


class FakeResponse:
    def __init__(
        self,
        *,
        output_parsed: object | None = None,
        output_text: str = "",
        output: object | None = None,
    ) -> None:
        self.output_parsed = output_parsed
        self.output_text = output_text
        self.output = output


class FakeResponsesAPI:
    def __init__(self, queued_responses: list[FakeResponse], *, error: Exception | None = None) -> None:
        self._queued_responses = list(queued_responses)
        self._error = error
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        if not self._queued_responses:
            raise AssertionError("No fake responses left to return.")
        return self._queued_responses.pop(0)


class FakeSDKClient:
    def __init__(self, queued_responses: list[FakeResponse], *, error: Exception | None = None) -> None:
        self.responses = FakeResponsesAPI(queued_responses, error=error)


class RecordingOpenAIClient:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def generate_structured(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.response


def build_brief_input() -> BriefInput:
    return BriefInput(
        product_name="Rocket",
        offer="AI campaign drafting",
        goal="demo_bookings",
        audience="Growth leads at B2B SaaS companies",
        geo=["US"],
        daily_budget_amount=150.0,
        budget_currency_code="USD",
        landing_page_url="https://example.com/demo",
        tone="Direct and credible",
        brand_notes="Avoid hype and keep it specific.",
    )


def build_campaign_plan() -> CampaignPlan:
    return CampaignPlan(
        campaign_name="Rocket Demo Bookings | US | Search",
        channel="google_search",
        keyword_themes=["ai performance marketing", "startup demo booking ads"],
        messaging_angles=["Launch faster", "Keep approval in the loop"],
        utm_campaign="rocket_demo_bookings_us_search",
        geo_targets=["US"],
        recommended_daily_budget_amount=150.0,
        budget_currency_code="USD",
    )


def test_generate_structured_returns_output_parsed_model() -> None:
    fake_sdk_client = FakeSDKClient(
        [FakeResponse(output_parsed=ExampleResponse(value="parsed"), output_text='{"value":"parsed"}')]
    )
    client = OpenAIResponsesClient(api_key="test-key", sdk_client=fake_sdk_client)

    result = client.generate_structured(
        system_prompt="system",
        user_prompt="user",
        response_model=ExampleResponse,
    )

    assert result.value == "parsed"
    assert fake_sdk_client.responses.calls[0]["model"] == "gpt-5.4"


def test_generate_structured_uses_lightweight_model_when_requested() -> None:
    fake_sdk_client = FakeSDKClient(
        [FakeResponse(output_parsed=ExampleResponse(value="parsed"), output_text='{"value":"parsed"}')]
    )
    client = OpenAIResponsesClient(api_key="test-key", sdk_client=fake_sdk_client)

    client.generate_structured(
        system_prompt="system",
        user_prompt="user",
        response_model=ExampleResponse,
        use_lightweight_model=True,
    )

    assert fake_sdk_client.responses.calls[0]["model"] == "gpt-5-mini"


def test_generate_structured_sends_sanitized_json_schema() -> None:
    fake_sdk_client = FakeSDKClient(
        [FakeResponse(output_parsed=build_brief_input(), output_text=build_brief_input().model_dump_json())]
    )
    client = OpenAIResponsesClient(api_key="test-key", sdk_client=fake_sdk_client)

    client.generate_structured(
        system_prompt="system",
        user_prompt="user",
        response_model=BriefInput,
    )

    text_payload = fake_sdk_client.responses.calls[0]["text"]
    assert isinstance(text_payload, dict)
    schema = text_payload["format"]["schema"]
    assert isinstance(schema, dict)
    assert '"format"' not in str(schema)


def test_generate_structured_retries_once_after_schema_validation_failure() -> None:
    fake_sdk_client = FakeSDKClient(
        [
            FakeResponse(output_text='{"count": 0}'),
            FakeResponse(output_text='{"count": 2}'),
        ]
    )
    client = OpenAIResponsesClient(api_key="test-key", sdk_client=fake_sdk_client)

    result = client.generate_structured(
        system_prompt="system",
        user_prompt="user",
        response_model=PositiveIntegerResponse,
    )

    assert result.count == 2
    assert len(fake_sdk_client.responses.calls) == 2
    retry_input = fake_sdk_client.responses.calls[1]["input"]
    assert isinstance(retry_input, list)
    assert any(
        "did not validate against the required schema" in str(message["content"])
        for message in retry_input
    )


def test_generate_structured_raises_after_second_validation_failure() -> None:
    fake_sdk_client = FakeSDKClient(
        [
            FakeResponse(output_text='{"count": 0}'),
            FakeResponse(output_text='{"count": -5}'),
        ]
    )
    client = OpenAIResponsesClient(api_key="test-key", sdk_client=fake_sdk_client)

    with pytest.raises(OpenAIResponseError, match="failed schema validation after one retry"):
        client.generate_structured(
            system_prompt="system",
            user_prompt="user",
            response_model=PositiveIntegerResponse,
        )


def test_generate_structured_logs_raw_response_only_in_debug_mode(caplog: pytest.LogCaptureFixture) -> None:
    fake_sdk_client = FakeSDKClient(
        [FakeResponse(output_parsed=ExampleResponse(value="parsed"), output_text='{"value":"parsed"}')]
    )
    client = OpenAIResponsesClient(api_key="test-key", sdk_client=fake_sdk_client)

    caplog.set_level(logging.INFO, logger="app.services.openai_client")
    client.generate_structured(
        system_prompt="system",
        user_prompt="user",
        response_model=ExampleResponse,
    )
    assert "Raw model response" not in caplog.text

    debug_sdk_client = FakeSDKClient(
        [FakeResponse(output_parsed=ExampleResponse(value="debug"), output_text='{"value":"debug"}')]
    )
    debug_client = OpenAIResponsesClient(
        api_key="test-key",
        sdk_client=debug_sdk_client,
        allow_sensitive_observability=True,
    )
    caplog.clear()
    caplog.set_level(logging.DEBUG, logger="app.services.openai_client")
    debug_client.generate_structured(
        system_prompt="system",
        user_prompt="user",
        response_model=ExampleResponse,
    )
    assert "Raw model response for gpt-5.4" in caplog.text


def test_generate_structured_wraps_sdk_errors_in_clean_exception() -> None:
    client = OpenAIResponsesClient(
        api_key="test-key",
        sdk_client=FakeSDKClient([], error=RuntimeError("gateway timeout")),
    )

    with pytest.raises(OpenAIResponseError, match="OpenAI Responses API request failed: gateway timeout"):
        client.generate_structured(
            system_prompt="system",
            user_prompt="user",
            response_model=ExampleResponse,
        )


def test_brief_parser_uses_lightweight_model_and_returns_typed_schema() -> None:
    brief = build_brief_input()
    client = RecordingOpenAIClient(response=brief)
    parser = BriefParser(openai_client=client)

    result = parser.parse({"offer": "AI campaign drafting"})

    assert result == brief
    assert client.calls[0]["response_model"] is BriefInput
    assert client.calls[0]["use_lightweight_model"] is True


def test_strategy_composer_returns_campaign_plan() -> None:
    plan = build_campaign_plan()
    client = RecordingOpenAIClient(response=plan)
    composer = StrategyComposer(openai_client=client)

    result = composer.compose(build_brief_input())

    assert result == plan
    assert client.calls[0]["response_model"] is CampaignPlan
    assert client.calls[0]["use_lightweight_model"] is False


def test_rsa_copy_generator_returns_variants() -> None:
    brief = build_brief_input()
    plan = build_campaign_plan()
    variants = [
        {
            "headlines": ["Book More Demos", "Launch Faster", "Keep Spend Controlled"],
            "descriptions": [
                "Turn a brief into a paused Google Ads draft.",
                "Keep approval in the loop before anything goes live.",
            ],
            "final_url": "https://example.com/demo",
            "path1": "demo",
            "path2": "book",
        }
        for _ in range(3)
    ]
    client = RecordingOpenAIClient(response=RSACopyGenerationResult.model_validate({"variants": variants}))
    generator = RSACopyGenerator(openai_client=client)

    result = generator.generate(brief=brief, campaign_plan=plan, variant_count=3)

    assert len(result) == 3
    assert client.calls[0]["response_model"].__name__ == "RSACopyGenerationResult"


def test_rsa_copy_generator_rejects_invalid_variant_count() -> None:
    generator = RSACopyGenerator(openai_client=RecordingOpenAIClient(response={"variants": []}))

    with pytest.raises(ValueError, match="variant_count must be between 3 and 5"):
        generator.generate(brief=build_brief_input(), campaign_plan=build_campaign_plan(), variant_count=2)
