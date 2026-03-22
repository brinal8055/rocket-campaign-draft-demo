from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from app.prompts import load_prompt, render_prompt
from app.services import OpenAIResponsesClient
from app.schemas import BriefInput, CampaignPlan, ResponsiveSearchAdVariant
from app.utils import traceable


class RSACopyGenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variants: list[ResponsiveSearchAdVariant] = Field(min_length=3, max_length=5)


@dataclass(slots=True)
class RSACopyGenerator:
    """Generate validated responsive search ad variants."""

    openai_client: OpenAIResponsesClient | None = None
    system_prompt: str = field(default_factory=lambda: load_prompt("rsa_copy_generator_system"))
    user_prompt_template_name: str = "rsa_copy_generator_user"
    model: str | None = None

    @traceable(run_type="chain", name="rsa_copy_generator")
    def generate(
        self,
        brief: BriefInput,
        campaign_plan: CampaignPlan,
        *,
        variant_count: int = 3,
    ) -> list[ResponsiveSearchAdVariant]:
        if variant_count < 3 or variant_count > 5:
            raise ValueError("variant_count must be between 3 and 5.")

        client = self._require_client()
        user_prompt = render_prompt(
            self.user_prompt_template_name,
            variant_count=variant_count,
            brief_json=brief.model_dump_json(indent=2),
            campaign_plan_json=campaign_plan.model_dump_json(indent=2),
        )
        result = client.generate_structured(
            system_prompt=self.system_prompt,
            user_prompt=user_prompt,
            response_model=RSACopyGenerationResult,
            model=self.model,
            use_lightweight_model=False,
        )
        return result.variants

    def _require_client(self) -> OpenAIResponsesClient:
        if self.openai_client is None:
            raise RuntimeError("RSACopyGenerator requires an OpenAIResponsesClient instance.")
        return self.openai_client
