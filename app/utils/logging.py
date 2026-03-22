from __future__ import annotations

import logging

from rich.logging import RichHandler


def configure_logging(level: str | int = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                markup=True,
                rich_tracebacks=True,
                show_path=False,
            )
        ],
        force=True,
    )

