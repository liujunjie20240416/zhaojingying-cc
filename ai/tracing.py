"""LangSmith tracing helpers for the LLM data path.

The project mixes LangChain/LangGraph calls with direct OpenAI-compatible
client calls. LangSmith automatically traces the LangChain path when the
LANGSMITH_* env vars are set; this module adds lightweight explicit traces for
the hand-built prompts and retrieval context that otherwise disappear.
"""

from __future__ import annotations

import os
from functools import wraps
from typing import Any, Callable

try:
    from langsmith import traceable as _langsmith_traceable
except Exception:  # pragma: no cover - tracing must never break chat.
    _langsmith_traceable = None


TRUE_VALUES = {"1", "true", "yes", "on"}
DEFAULT_PROJECT = "zhaojingying-cc"


def langsmith_enabled() -> bool:
    """Return whether LangSmith tracing is explicitly enabled."""
    tracing = (
        os.getenv("LANGSMITH_TRACING")
        or os.getenv("LANGCHAIN_TRACING_V2")
        or ""
    ).strip().lower()
    return tracing in TRUE_VALUES and bool(os.getenv("LANGSMITH_API_KEY", "").strip())


def configure_langsmith_defaults() -> None:
    """Set safe LangSmith defaults after .env has been loaded."""
    if not os.getenv("LANGSMITH_PROJECT"):
        os.environ["LANGSMITH_PROJECT"] = DEFAULT_PROJECT
    if langsmith_enabled() and not os.getenv("LANGCHAIN_TRACING_V2"):
        os.environ["LANGCHAIN_TRACING_V2"] = os.getenv("LANGSMITH_TRACING", "")


def traceable(name: str, run_type: str = "chain") -> Callable:
    """Optional LangSmith decorator with a no-op fallback."""
    if _langsmith_traceable is None:
        def decorator(func: Callable) -> Callable:
            return func
        return decorator
    return _langsmith_traceable(name=name, run_type=run_type)


def record_trace(
    name: str,
    inputs: dict[str, Any],
    outputs: Any = None,
    *,
    run_type: str = "chain",
    metadata: dict[str, Any] | None = None,
) -> Any:
    """Record a standalone trace span without changing caller behavior."""
    if _langsmith_traceable is None or not langsmith_enabled():
        return outputs

    try:
        @_langsmith_traceable(name=name, run_type=run_type, metadata=metadata or {})
        def _record(payload: dict[str, Any]) -> Any:
            return payload.get("outputs")

        return _record({"inputs": inputs, "outputs": outputs})
    except Exception:
        return outputs


def serialize_message(message: Any) -> dict[str, Any]:
    """Convert LangChain messages or simple dict messages into JSON-safe shape."""
    if isinstance(message, dict):
        return dict(message)

    data: dict[str, Any] = {}
    msg_type = getattr(message, "type", None)
    if msg_type:
        data["type"] = msg_type
    role = getattr(message, "role", None)
    if role:
        data["role"] = role
    data["content"] = getattr(message, "content", "")

    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        data["tool_calls"] = tool_calls
    usage = getattr(message, "usage_metadata", None)
    if usage:
        data["usage_metadata"] = usage
    return data


def serialize_messages(messages: Any) -> list[dict[str, Any]]:
    return [serialize_message(message) for message in messages or []]


def traced_method(name: str, run_type: str = "chain") -> Callable:
    """Decorator for methods that should become no-ops when LangSmith is absent."""
    def decorator(func: Callable) -> Callable:
        if _langsmith_traceable is None:
            return func

        traced_func = _langsmith_traceable(name=name, run_type=run_type)(func)

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return traced_func(*args, **kwargs)

        return wrapper

    return decorator
