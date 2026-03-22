from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.prompts import load_prompt, render_prompt
from app.services import OpenAIResponsesClient
from app.schemas import BriefInput
from app.utils import traceable


@dataclass(slots=True)
class BriefParser:
    """Transform raw input into a normalized brief."""

    openai_client: OpenAIResponsesClient | None = None
    system_prompt: str = field(default_factory=lambda: load_prompt("brief_parser_system"))
    user_prompt_template_name: str = "brief_parser_user"
    model: str | None = None

    @traceable(run_type="chain", name="brief_parser")
    def parse(self, raw_brief: Mapping[str, Any]) -> BriefInput:
        client = self._require_client()
        user_prompt = render_prompt(
            self.user_prompt_template_name,
            raw_brief_json=json.dumps(raw_brief, indent=2, sort_keys=True, default=str),
        )
        return client.generate_structured(
            system_prompt=self.system_prompt,
            user_prompt=user_prompt,
            response_model=BriefInput,
            model=self.model,
            use_lightweight_model=True,
        )

    def _require_client(self) -> OpenAIResponsesClient:
        if self.openai_client is None:
            raise RuntimeError("BriefParser requires an OpenAIResponsesClient instance.")
        return self.openai_client
