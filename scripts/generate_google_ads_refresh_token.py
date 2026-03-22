#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

GOOGLE_ADS_SCOPE = "https://www.googleapis.com/auth/adwords"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a Google Ads OAuth refresh token from an installed-app client secret JSON.",
    )
    parser.add_argument(
        "--client-secret-json",
        required=True,
        help="Path to the OAuth client secret JSON downloaded from Google Cloud.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not try to open a browser automatically; print the auth URL instead.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Local port for the OAuth callback server. Defaults to a random free port.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    client_secret_path = Path(args.client_secret_json).expanduser().resolve()
    if not client_secret_path.exists():
        print(f"Client secret JSON not found: {client_secret_path}", file=sys.stderr)
        return 1

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        print(
            "Missing dependency: google-auth-oauthlib. Install project dependencies first.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secret_path),
        scopes=[GOOGLE_ADS_SCOPE],
    )

    credentials = flow.run_local_server(
        host="localhost",
        port=args.port,
        open_browser=not args.no_browser,
        authorization_prompt_message=(
            "Open this URL in your browser to authorize Google Ads access:\n{url}\n"
        ),
        success_message=(
            "Authorization complete. You can close this tab and return to the terminal."
        ),
    )

    result = {
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "refresh_token": credentials.refresh_token,
        "scopes": list(credentials.scopes or []),
        "token_uri": credentials.token_uri,
    }

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
