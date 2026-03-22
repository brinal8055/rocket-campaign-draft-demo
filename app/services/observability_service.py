from __future__ import annotations

import os
from dataclasses import dataclass

from app.utils import AppSettings


@dataclass(slots=True)
class ObservabilityService:
    """Configure LangSmith tracing for the current process."""

    api_key: str | None = None
    project: str | None = None
    tracing_enabled: bool = True
    workspace_id: str | None = None

    @classmethod
    def from_settings(cls, settings: AppSettings) -> ObservabilityService:
        return cls(
            api_key=settings.langsmith_api_key.get_secret_value(),
            project=settings.langsmith_project,
            tracing_enabled=settings.langsmith_tracing,
            workspace_id=settings.langsmith_workspace_id,
        )

    def configure(self) -> None:
        if not self.tracing_enabled or not self.api_key or not self.project:
            return

        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = self.api_key
        os.environ["LANGSMITH_PROJECT"] = self.project

        if self.workspace_id:
            os.environ["LANGSMITH_WORKSPACE_ID"] = self.workspace_id

    def start_run(self, metadata: dict[str, object] | None = None) -> dict[str, object]:
        self.configure()
        return metadata or {}
