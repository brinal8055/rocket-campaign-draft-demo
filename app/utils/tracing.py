from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

F = TypeVar("F", bound=Callable[..., Any])


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
    try:
        from langsmith.wrappers import wrap_openai
    except ImportError:
        return client

    return wrap_openai(client)
