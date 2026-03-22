# Rocket Campaign Draft Demo

Clean Python scaffold for the first Rocket demo wedge:

`structured brief -> campaign plan + copy -> paused Google Ads draft -> approval request`

This repository currently provides the project skeleton only. External integrations and business logic are intentionally stubbed so we can implement them incrementally without reworking the foundation.

## Stack

- Python 3.11
- Pydantic v2
- `pydantic-settings` + `.env` loading
- `rich` logging
- `pytest`

## Project Layout

```text
app/
  agents/
  orchestration/
  prompts/
  schemas/
  services/
  utils/
  validators/
scripts/
tests/
```

## Quickstart

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
python scripts/run_demo.py
pytest
```

## Current Behavior

The CLI does three things today:

1. Loads configuration strictly from environment variables and `.env`.
2. Configures rich logging.
3. Prints a scaffold preview of the planned demo pipeline.

It does not yet call OpenAI, Google Ads, LangSmith, CrewAI, or n8n.

## Configuration

Required settings live in `.env.example`. Startup failures are intentionally strict:

- missing required variables fail fast
- invalid URLs or malformed Google Ads IDs fail fast
- unknown keys in the `.env` file fail fast

This keeps integration issues visible before we add real execution logic.

## Next Implementation Steps

1. Implement typed brief ingestion and validation.
2. Add strategy and copy generation prompts.
3. Wire Google Ads draft creation in paused state.
4. Send approval payloads to n8n.
5. Add LangSmith tracing around the full run.

