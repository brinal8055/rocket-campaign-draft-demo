# Rocket Campaign Draft Demo

End-to-end Python demo for Rocket's first working wedge:

`structured brief -> campaign plan + RSA copy -> paused Google Ads draft -> approval webhook`

The demo uses the OpenAI Responses API for structured generation, the official Google Ads Python client for draft creation, n8n for approval delivery, and LangSmith for tracing.

## Stack

- Python 3.11
- Pydantic v2
- `pydantic-settings` + `.env`
- `rich`
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
artifacts/
```

## Quickstart

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
PYTHONPATH=. python scripts/google_ads_smoke_test.py --env-file .env
PYTHONPATH=. python scripts/run_demo.py --brief-file artifacts/demo_brief.json --env-file .env
pytest
```

## What The Demo Does

1. Loads a brief JSON file and normalizes it into `BriefInput`.
2. Generates a `CampaignPlan`.
3. Generates 3-5 responsive search ad variants.
4. Validates all structured outputs before any external mutation.
5. Creates a paused Search campaign, one ad group, keywords, geo targets, and one RSA in Google Ads.
6. Sends the approval payload to n8n.
7. Saves a structured artifact to `artifacts/last_run.json`.

The flow writes checkpoints as it progresses. If a downstream step fails after Google Ads resources are created, the artifact still captures the completed work and any partial state that should be reviewed.

## Required Configuration

Required keys are listed in [.env.example](/Users/brinalsavsaviya/Documents/Code/AI/Agents/rocket-campaign-draft-demo/.env.example). Important settings:

- `GOOGLE_ADS_USE_TEST_ACCOUNT=true` enforces that the configured customer must be a Google Ads test account before the flow will mutate anything.
- `N8N_APPROVAL_WEBHOOK_SECRET` is optional but recommended. If set, the app sends it using `N8N_APPROVAL_WEBHOOK_SECRET_HEADER`.
- `ALLOW_SENSITIVE_OBSERVABILITY=false` is the safe default. Keep it `false` unless you explicitly want full prompts, payloads, and model I/O visible in debug logs and LangSmith traces.

## Budget Currency Safety

Budgets are currency-aware now:

- `BriefInput` uses `daily_budget_amount` and `budget_currency_code`
- `CampaignPlan` uses `recommended_daily_budget_amount` and `budget_currency_code`

For backward-compatible input parsing, the app still accepts legacy JSON keys like `daily_budget_usd` and `recommended_daily_budget_usd`, but the flow will refuse to mutate Google Ads unless the plan currency matches the configured account currency exactly.

## Scripts

- [scripts/run_demo.py](/Users/brinalsavsaviya/Documents/Code/AI/Agents/rocket-campaign-draft-demo/scripts/run_demo.py): run the full demo flow.
- [scripts/google_ads_smoke_test.py](/Users/brinalsavsaviya/Documents/Code/AI/Agents/rocket-campaign-draft-demo/scripts/google_ads_smoke_test.py): validate Google Ads auth and print account metadata without mutating anything.
- [scripts/generate_google_ads_refresh_token.py](/Users/brinalsavsaviya/Documents/Code/AI/Agents/rocket-campaign-draft-demo/scripts/generate_google_ads_refresh_token.py): generate a refresh token from an installed-app OAuth client JSON.

## Observability

LangSmith tracing is enabled through the runtime configuration. By default, traces are redacted enough to avoid shipping raw prompts, briefs, URLs, customer IDs, and approval payloads. If you want full local-demo visibility, set:

```env
ALLOW_SENSITIVE_OBSERVABILITY=true
```

That opt-in also enables wrapped OpenAI request traces and raw model response logging in DEBUG mode.
