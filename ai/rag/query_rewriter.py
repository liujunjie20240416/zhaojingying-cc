import json
from openai import OpenAI

from ai.config import (
    llm_api_base,
    llm_api_key,
    llm_model,
    require_llm_config,
    sub_llm_timeout,
)
from ai.tracing import record_trace


class QueryRewriter:
    """Turn a recall request into a structured retrieval plan."""

    def __init__(self, api_key: str = "", api_base: str = ""):
        if not api_key and not api_base:
            require_llm_config()
        self.client = OpenAI(
            api_key=api_key or llm_api_key(),
            base_url=api_base or llm_api_base(),
            # 请求路径上的调用一律有界：SDK 默认 read=600s（还是 inter-byte，
            # 收到任意字节就重置，连单次调用的总墙钟都不限），配默认的 2 次重试
            # 单点最坏能到 30 分钟。max_retries=1 保留一次抖动重试，同时让
            # 「最坏 = 2 × timeout」是个能算的账。
            timeout=sub_llm_timeout(),
            max_retries=1,
        )

    def rewrite(self, query: str) -> list[str]:
        """Compatibility wrapper for callers that only need rewritten queries."""
        return self.plan(query)["queries"]

    def plan(
        self,
        query: str,
        *,
        fallback_intent: dict | None = None,
        imported_chat_available: bool = False,
        recent_dialogue: str = "",
    ) -> dict:
        """Return queries plus temporal/source/evidence requirements in one call."""
        fallback = _fallback_plan(query, fallback_intent, imported_chat_available)
        prompt = f"""你是伴侣聊天的检索规划助手。理解用户是在问什么，再制定检索计划。

规则：
- queries: 2-3 个适合检索的改写，必须包含用户原话或同义表达。
- temporal_anchor: origin|early|historical|specific_time|recent|current|unknown。
  origin 指两人最初相识、第一次联系、怎么熟起来；不要只靠字面词判断，要理解语义。
- source_policy: import_preferred|online_preferred|balanced|any。
  问早期关系、导入历史时通常 import_preferred；问近期承诺通常 online_preferred；关系演变通常 balanced。
- evidence_policy: raw_required|mixed|semantic_first。问具体经过、时间、原话时 raw_required。
- 不知道时用 unknown/any，不要编造阶段。
- Imported Chat 是否可用：{imported_chat_available}。
- 只输出 JSON 对象，不要 Markdown。

最近对话（仅用于理解用户当前这句的省略指代；不要把它当作检索目标）：
{recent_dialogue or "（无）"}

用户原话："{query}"

输出格式：{{"queries":["查询1","查询2"],"temporal_anchor":"unknown","source_policy":"any","evidence_policy":"mixed"}}"""

        trace_inputs = {
            "model": llm_model(),
            "query": query,
            "messages": [{"role": "user", "content": prompt}],
        }
        record_trace("rag.query_rewriter.prompt", trace_inputs)
        resp = self.client.chat.completions.create(
            model=llm_model(),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=320,
        )
        content = resp.choices[0].message.content.strip()
        # 清理 markdown code fence
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
        try:
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                return fallback
            rewrites = [q.strip() for q in parsed.get("queries", []) if isinstance(q, str) and q.strip()]
            if query not in rewrites:
                rewrites.insert(0, query)
            result = {
                "queries": rewrites[:4],
                "temporal_anchor": _allowed(parsed.get("temporal_anchor"), {
                    "origin", "early", "historical", "specific_time", "recent", "current", "unknown",
                }, fallback["temporal_anchor"]),
                "source_policy": _allowed(parsed.get("source_policy"), {
                    "import_preferred", "online_preferred", "balanced", "any",
                }, fallback["source_policy"]),
                "evidence_policy": _allowed(parsed.get("evidence_policy"), {
                    "raw_required", "mixed", "semantic_first",
                }, fallback["evidence_policy"]),
            }
            record_trace(
                "rag.query_rewriter.output",
                trace_inputs,
                {"raw_content": content, "plan": result},
                run_type="llm",
            )
            return result
        except json.JSONDecodeError:
            record_trace(
                "rag.query_rewriter.output",
                trace_inputs,
                {"raw_content": content, "plan": fallback, "error": "JSONDecodeError"},
                run_type="llm",
            )
            return fallback


def _allowed(value, allowed: set[str], fallback: str) -> str:
    value = str(value or "")
    return value if value in allowed else fallback


def _fallback_plan(query: str, intent: dict | None, imported_available: bool) -> dict:
    intent = intent or {}
    time_mode = str(intent.get("time_mode", "unknown"))
    if time_mode not in {"early", "historical", "specific_time", "recent", "current"}:
        time_mode = "unknown"
    source_policy = "any"
    if imported_available and time_mode in {"early", "historical", "specific_time"}:
        source_policy = "import_preferred"
    elif time_mode == "recent":
        source_policy = "online_preferred"
    elif imported_available and intent.get("target_subject") == "relationship":
        source_policy = "balanced"
    evidence_policy = "raw_required" if intent.get("needs_raw_chat") else "mixed"
    return {
        "queries": [query],
        "temporal_anchor": time_mode,
        "source_policy": source_policy,
        "evidence_policy": evidence_policy,
    }
