from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, TypeVar, cast

F = TypeVar("F", bound=Callable[..., Any])
SENSITIVE_OBSERVABILITY_ENV = "ROCKET_ALLOW_SENSITIVE_OBSERVABILITY"


def traceable(*args: Any, **kwargs: Any) -> Any:
    try:
        from langsmith import traceable as langsmith_traceable
    except ImportError:
        if args and callable(args[0]) and len(args) == 1 and not kwargs:
            return args[0]

        def decorator(func: F) -> F:
            return func

        return decorator

    return langsmith_traceable(*args, **kwargs)


def wrap_openai_client(client: Any) -> Any:
    if not sensitive_observability_enabled():
        return client

    try:
        from langsmith.wrappers import wrap_openai
    except ImportError:
        return client

    return wrap_openai(client)


def set_sensitive_observability(enabled: bool) -> None:
    os.environ[SENSITIVE_OBSERVABILITY_ENV] = "true" if enabled else "false"


def sensitive_observability_enabled() -> bool:
    return os.environ.get(SENSITIVE_OBSERVABILITY_ENV, "false").casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def mask_identifier(value: str | None, *, visible_suffix: int = 4) -> str:
    if not value:
        return "<unset>"
    if len(value) <= visible_suffix:
        return "*" * len(value)
    return f"{'*' * max(4, len(value) - visible_suffix)}{value[-visible_suffix:]}"
