#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.orchestration.flow import DemoFlowError, RocketDemoFlow
from app.services.google_ads_service import GoogleAdsServiceError
from app.services.n8n_service import N8NServiceError
from app.services.openai_client import OpenAIResponseError
from app.utils import ConfigurationError, configure_logging, load_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Rocket brief-to-paused-draft demo flow.",
    )
    parser.add_argument(
        "--brief-file",
        required=True,
        help="Path to the input brief JSON file.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to the dotenv file to load. Defaults to .env.",
    )
    parser.add_argument(
        "--artifact-path",
        default="artifacts/last_run.json",
        help="Where to save the final structured run artifact.",
    )
    parser.add_argument(
        "--rsa-variant-count",
        type=int,
        default=3,
        help="How many RSA variants to generate. Must be between 3 and 5.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the final structured result as JSON instead of a rich terminal summary.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    return parser


def load_brief_json(path: str | Path) -> dict[str, Any]:
    brief_path = Path(path)
    try:
        parsed = json.loads(brief_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DemoFlowError(f"Brief file not found: {brief_path}") from exc
    except OSError as exc:
        raise DemoFlowError(f"Unable to read brief file: {brief_path}") from exc
    except json.JSONDecodeError as exc:
        raise DemoFlowError(f"Brief file is not valid JSON: {brief_path}") from exc

    if not isinstance(parsed, dict):
        raise DemoFlowError("Brief JSON must be a single object at the top level.")

    return parsed


def render_run_summary(console: Console, result_json: dict[str, Any]) -> None:
    overview = Table(title="Rocket Demo Result")
    overview.add_column("Field", style="cyan")
    overview.add_column("Value", style="green")
    overview.add_row("Environment", str(result_json["environment"]))
    overview.add_row("Campaign", str(result_json["campaign_plan"]["campaign_name"]))
    overview.add_row("Status", str(result_json["draft_creation_result"]["campaign_status"]))
    overview.add_row("Campaign Resource", str(result_json["draft_creation_result"]["campaign_resource_name"]))
    overview.add_row("Ad Group Resource", str(result_json["draft_creation_result"]["ad_group_resource_name"]))
    overview.add_row("Approval", str(result_json["draft_creation_result"]["approval_status"]))
    overview.add_row("Artifact", str(result_json["artifact_path"]))
    console.print(overview)

    brief_table = Table(title="Brief")
    brief_table.add_column("Field", style="cyan")
    brief_table.add_column("Value")
    brief = result_json["brief"]
    brief_table.add_row("Product", str(brief["product_name"]))
    brief_table.add_row("Goal", str(brief["goal"]))
    brief_table.add_row("Audience", str(brief["audience"]))
    brief_table.add_row("Geo", ", ".join(brief["geo"]))
    brief_table.add_row("Daily Budget", f"${brief['daily_budget_usd']:.2f}")
    brief_table.add_row("Landing Page", str(brief["landing_page_url"]))
    console.print(brief_table)

    plan = result_json["campaign_plan"]
    plan_table = Table(title="Campaign Plan")
    plan_table.add_column("Field", style="cyan")
    plan_table.add_column("Value")
    plan_table.add_row("Keyword Themes", ", ".join(plan["keyword_themes"]))
    plan_table.add_row("Messaging Angles", ", ".join(plan["messaging_angles"]))
    plan_table.add_row("Geo Targets", ", ".join(plan["geo_targets"]))
    plan_table.add_row("UTM Campaign", str(plan["utm_campaign"]))
    console.print(plan_table)

    rsa_table = Table(title="RSA Variants")
    rsa_table.add_column("Variant", style="cyan")
    rsa_table.add_column("Headlines")
    rsa_table.add_column("Descriptions")
    rsa_table.add_column("Final URL")
    for index, variant in enumerate(result_json["rsa_variants"], start=1):
        rsa_table.add_row(
            f"V{index}",
            " | ".join(variant["headlines"]),
            " | ".join(variant["descriptions"]),
            str(variant["final_url"]),
        )
    console.print(rsa_table)

    stages_table = Table(title="Stages")
    stages_table.add_column("Stage", style="cyan")
    stages_table.add_column("Status", style="green")
    stages_table.add_column("Description")
    for stage in result_json["stages"]:
        stages_table.add_row(stage["name"], stage["status"], stage["description"])
    console.print(stages_table)

    console.print("[bold]Structured Result[/bold]")
    console.print(
        Syntax(
            json.dumps(result_json, indent=2),
            "json",
            word_wrap=True,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console(stderr=True)

    try:
        settings = load_settings(args.env_file)
    except ConfigurationError as exc:
        console.print(f"[bold red]Configuration error[/bold red]\n{exc}")
        return 1

    configure_logging("DEBUG" if args.verbose else settings.log_level)

    try:
        raw_brief = load_brief_json(args.brief_file)
        result = RocketDemoFlow(settings=settings).run(
            raw_brief=raw_brief,
            artifact_path=args.artifact_path,
            rsa_variant_count=args.rsa_variant_count,
        )
    except (
        DemoFlowError,
        GoogleAdsServiceError,
        N8NServiceError,
        OpenAIResponseError,
        ValueError,
    ) as exc:
        console.print(f"[bold red]Demo flow failed[/bold red]\n{exc}")
        return 1

    result_json = result.model_dump(mode="json")
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        render_run_summary(Console(), result_json)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
