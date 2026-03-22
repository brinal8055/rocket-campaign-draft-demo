from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Any

PROMPTS_DIR = Path(__file__).resolve().parent


def load_prompt(name: str) -> str:
    prompt_path = PROMPTS_DIR / f"{name}.txt"
    return prompt_path.read_text(encoding="utf-8").strip()


def render_prompt(name: str, **context: Any) -> str:
    serialized_context = {
        key: value if isinstance(value, str) else str(value)
        for key, value in context.items()
    }
    return Template(load_prompt(name)).substitute(serialized_context)


__all__ = ["load_prompt", "render_prompt"]
