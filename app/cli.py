from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence

from rich.console import Console
from rich.table import Table

from app.orchestration.demo_flow import DemoOrchestrator
from app.utils import ConfigurationError, configure_logging, load_settings

LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview the Rocket campaign draft demo scaffold.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to the dotenv file to load. Defaults to .env.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the scaffold summary as JSON.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging for local scaffolding runs.",
    )
    return parser


def render_summary(console: Console, summary_json: dict[str, object]) -> None:
    stages = summary_json["stages"]
    notes = summary_json["notes"]

    table = Table(title="Rocket Demo Scaffold")
    table.add_column("Stage", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Description")

    for stage in stages:
        table.add_row(stage["name"], stage["status"], stage["description"])

    console.print(table)
    console.print("[bold]Notes[/bold]")
    for note in notes:
        console.print(f"- {note}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    console = Console(stderr=True)

    try:
        settings = load_settings(args.env_file)
    except ConfigurationError as exc:
        console.print(f"[bold red]Configuration error[/bold red]\n{exc}")
        return 1

    configure_logging("DEBUG" if args.verbose else settings.log_level)
    LOGGER.info("Configuration loaded for environment '%s'.", settings.environment)

    summary = DemoOrchestrator(settings=settings).preview()
    summary_json = summary.model_dump(mode="json")

    if args.json:
        print(summary.model_dump_json(indent=2))
    else:
        render_summary(console=Console(), summary_json=summary_json)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

