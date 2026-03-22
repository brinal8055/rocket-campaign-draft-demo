from __future__ import annotations

from app.schemas import DraftCreationResult


def ensure_paused_campaign_result(result: DraftCreationResult) -> DraftCreationResult:
    if result.campaign_status != "PAUSED":
        raise ValueError("Draft campaigns must remain in PAUSED status.")
    return result

