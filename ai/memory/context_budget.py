"""Token-budgeted context assembly diagnostics.

The estimator is deliberately conservative for Chinese conversation.  It is
used to decide what to compact before a provider reports an exact usage value;
the reported value from the previous turn remains available for calibration.
"""
from __future__ import annotations

import math
import os
import re
from typing import Mapping


# Existing Online Chat is tiny (P90: 40 characters per turn), while observed
# end-to-end prompts reached ~26K tokens.  These defaults leave room for the
# static prompt and a response without compacting routine short conversations.
SOFT_INPUT_TOKEN_BUDGET = int(os.getenv("CONTEXT_SOFT_INPUT_TOKENS", "32000"))
HARD_INPUT_TOKEN_BUDGET = int(os.getenv("CONTEXT_HARD_INPUT_TOKENS", "40000"))
RECENT_HISTORY_TOKEN_BUDGET = int(os.getenv("CONTEXT_RECENT_HISTORY_TOKENS", "6000"))
WORKING_HISTORY_TOKEN_BUDGET = int(os.getenv("CONTEXT_WORKING_HISTORY_TOKENS", "9000"))
SUMMARY_TOKEN_BUDGET = int(os.getenv("CONTEXT_SUMMARY_TOKENS", "1800"))
MEMORY_CONTEXT_TOKEN_BUDGET = int(os.getenv("CONTEXT_MEMORY_TOKENS", "6000"))

_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def estimate_tokens(text: object) -> int:
    """Estimate tokens without a provider round-trip.

    Chinese text generally tokenizes more densely than English prose.  This
    estimate intentionally rounds up; exact provider usage is recorded after
    the response and shown in diagnostics as the calibration anchor.
    """
    value = str(text or "")
    cjk_count = len(_CJK.findall(value))
    other_count = len(value) - cjk_count
    return max(1, math.ceil(cjk_count * 0.9 + other_count / 3.2)) if value else 0


def truncate_to_token_budget(text: str, budget: int) -> str:
    """Keep the highest-ranked prefix of a context block within its budget."""
    if budget <= 0:
        return ""
    if estimate_tokens(text) <= budget:
        return text
    marker = "\n[其余低优先级检索证据因上下文预算未注入]"
    content_budget = budget - estimate_tokens(marker)
    if content_budget <= 0:
        return ""
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_tokens(text[:middle]) <= content_budget:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + marker


def assemble_memory_sections(
    sections: list[dict[str, str]],
    budget: int,
    memory_intent: Mapping[str, object] | None = None,
) -> str:
    """Select retrieval context by question type before enforcing a total cap.

    A quote request must keep raw dialogue; an evolution request needs state
    transitions and period capsules; a current-fact question needs Semantic
    Memory.  This is deterministic routing, not another LLM invocation.
    """
    active = [section for section in sections if section.get("text")]
    if not active or budget <= 0:
        return ""
    intent = memory_intent or {}
    weights = _memory_section_weights(intent)
    ranked = sorted(
        active,
        key=lambda section: weights.get(section.get("kind", ""), 0.01),
        reverse=True,
    )
    total_weight = sum(weights.get(section.get("kind", ""), 0.01) for section in ranked)
    remaining = budget
    parts: list[str] = []
    for index, section in enumerate(ranked):
        weight = weights.get(section.get("kind", ""), 0.01)
        slots_left = len(ranked) - index - 1
        # Give every selected section a small usable floor when the budget
        # permits, then allocate the rest proportionally by intent.
        reserved_for_rest = min(120 * slots_left, max(0, remaining - 80))
        proportional = int(budget * (weight / total_weight)) if total_weight else 0
        section_budget = min(remaining - reserved_for_rest, max(80, proportional))
        if section_budget <= 0:
            continue
        text = truncate_to_token_budget(section["text"], section_budget)
        if text:
            parts.append(text)
            remaining -= estimate_tokens(text)
        if remaining <= 0:
            break
    return "\n\n".join(parts)


def _memory_section_weights(intent: Mapping[str, object]) -> dict[str, float]:
    text_mode = str(intent.get("request_mode", ""))
    time_mode = str(intent.get("time_mode", "any"))
    category = str(intent.get("category_hint", "any"))
    trajectory = bool(intent.get("needs_state_trajectory", False))
    if text_mode == "quote":
        return {
            "raw_evidence": 0.70, "semantic": 0.12, "trajectory": 0.09,
            "collapse": 0.05, "topic": 0.02, "time_scope": 0.02,
        }
    if trajectory or time_mode in {"historical", "early", "specific_time"}:
        return {
            "trajectory": 0.30, "collapse": 0.25, "raw_evidence": 0.24,
            "semantic": 0.16, "relationship_overview": 0.03, "time_scope": 0.02,
        }
    if category == "relationship":
        return {
            "relationship_overview": 0.30, "collapse": 0.24,
            "semantic": 0.20, "raw_evidence": 0.18, "topic": 0.05,
            "time_scope": 0.03,
        }
    return {
        "semantic": 0.55, "raw_evidence": 0.25, "topic": 0.10,
        "collapse": 0.05, "trajectory": 0.03, "time_scope": 0.02,
    }


def project_dynamic_context(
    *,
    stable_prefix: str,
    recent_messages: str,
    summary: str,
    memory_context: str,
) -> dict[str, object]:
    """Apply a deterministic soft/hard budget before the final LLM request.

    This is the Claude-Code-style *read-time projection* layer: authoritative
    data stays in the database, while low-priority retrieval evidence shrinks
    first.  It makes no model call and therefore adds no per-turn LLM tokens.
    """
    fixed_tokens = estimate_tokens(stable_prefix) + estimate_tokens(recent_messages)
    summary = truncate_to_token_budget(summary, SUMMARY_TOKEN_BUDGET)
    summary_tokens = estimate_tokens(summary)
    soft_memory_budget = max(0, SOFT_INPUT_TOKEN_BUDGET - fixed_tokens - summary_tokens)
    hard_memory_budget = max(0, HARD_INPUT_TOKEN_BUDGET - fixed_tokens - summary_tokens)
    memory_budget = min(MEMORY_CONTEXT_TOKEN_BUDGET, soft_memory_budget, hard_memory_budget)
    projected_memory = truncate_to_token_budget(memory_context, memory_budget)
    projected_total = fixed_tokens + summary_tokens + estimate_tokens(projected_memory)

    # A very large static profile cannot be solved by deleting history.  The
    # caller receives this pressure signal for diagnostics rather than silently
    # pretending the prompt fits.
    return {
        "summary": summary,
        "memory_context": projected_memory,
        "fixed_tokens": fixed_tokens,
        "memory_budget": memory_budget,
        "projected_total": projected_total,
        "pressure": (
            "hard" if projected_total >= HARD_INPUT_TOKEN_BUDGET
            else "soft" if projected_total >= SOFT_INPUT_TOKEN_BUDGET
            else "normal"
        ),
    }


def context_diagnostics(components: Mapping[str, object], *, last_usage: int = 0) -> dict:
    token_components = {
        name: estimate_tokens(value)
        for name, value in components.items()
        if value
    }
    estimated_total = sum(token_components.values())
    return {
        "estimated_input_tokens": estimated_total,
        "last_provider_input_tokens": int(last_usage or 0),
        "soft_input_budget": SOFT_INPUT_TOKEN_BUDGET,
        "hard_input_budget": HARD_INPUT_TOKEN_BUDGET,
        "recent_history_budget": RECENT_HISTORY_TOKEN_BUDGET,
        "components": token_components,
        "pressure": (
            "hard" if estimated_total >= HARD_INPUT_TOKEN_BUDGET
            else "soft" if estimated_total >= SOFT_INPUT_TOKEN_BUDGET
            else "normal"
        ),
    }
