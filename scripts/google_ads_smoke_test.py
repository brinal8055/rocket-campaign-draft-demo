#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.google_ads_service import GoogleAdsService, GoogleAdsServiceError
from app.utils import ConfigurationError, configure_logging, load_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate Google Ads auth and print account metadata without mutating anything.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to the dotenv file to load. Defaults to .env.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the account metadata as JSON.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    return parser


def render_metadata_table(console: Console, metadata: dict[str, object]) -> None:
    table = Table(title="Google Ads Account Metadata")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")

    for key, value in metadata.items():
        table.add_row(key, str(value))

    console.print(table)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    console = Console(stderr=True)

    try:
        settings = load_settings(args.env_file)
    except ConfigurationError as exc:
        console.print(f"[bold red]Configuration error[/bold red]\n{exc}")
        return 1

    configure_logging("DEBUG" if args.verbose else settings.log_level)
    service = GoogleAdsService.from_settings(settings)

    try:
        metadata = service.validate_auth()
    except GoogleAdsServiceError as exc:
        console.print(f"[bold red]Google Ads auth check failed[/bold red]\n{exc}")
        return 1

    if args.json:
        print(json.dumps(metadata, indent=2, sort_keys=True))
    else:
        render_metadata_table(Console(), metadata)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
