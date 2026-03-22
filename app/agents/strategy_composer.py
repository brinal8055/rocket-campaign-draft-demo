from __future__ import annotations

from dataclasses import dataclass, field

from app.prompts import load_prompt, render_prompt
from app.services import OpenAIResponsesClient
from app.schemas import BriefInput, CampaignPlan
from app.utils import traceable


@dataclass(slots=True)
class StrategyComposer:
    """Translate a normalized brief into campaign strategy."""

    openai_client: OpenAIResponsesClient | None = None
    system_prompt: str = field(default_factory=lambda: load_prompt("strategy_composer_system"))
    user_prompt_template_name: str = "strategy_composer_user"
    model: str | None = None

    @traceable(run_type="chain", name="strategy_composer")
    def compose(self, brief: BriefInput) -> CampaignPlan:
        client = self._require_client()
        user_prompt = render_prompt(
            self.user_prompt_template_name,
            brief_json=brief.model_dump_json(indent=2),
        )
        return client.generate_structured(
            system_prompt=self.system_prompt,
            user_prompt=user_prompt,
            response_model=CampaignPlan,
            model=self.model,
            use_lightweight_model=False,
        )

    def _require_client(self) -> OpenAIResponsesClient:
        if self.openai_client is None:
            raise RuntimeError("StrategyComposer requires an OpenAIResponsesClient instance.")
        return self.openai_client
