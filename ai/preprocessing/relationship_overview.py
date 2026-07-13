"""Relationship overview Reduce step.

Map 阶段已经把每个时间块压缩成摘要、关键事件和关系片段。
这里再按时间顺序做一次 Reduce，总结关系阶段、当前状态和互动模式。
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI

from ai.config import llm_api_base, llm_api_key, llm_model, require_llm_config
from ai.tracing import record_trace

REDUCE_MAX_WORKERS = 3


def _get_client(api_key: str = "", api_base: str = ""):
    if not api_key and not api_base:
        require_llm_config()
    return OpenAI(
        api_key=api_key or llm_api_key(),
        base_url=api_base or llm_api_base(),
        timeout=60,
    )


def analyze_relationship_overview(
    chunk_results: list[dict],
    chunks: list[dict],
    target_name: str,
    api_key: str = "",
    api_base: str = "",
) -> dict:
    """生成关系演变 overview 和 timeline_json 数据。

    返回:
      {
        "overview": str,
        "timeline": {"stages": [...], "current_state": ..., ...}
      }
    """
    entries = _build_timeline_entries(chunk_results, chunks)
    if not entries:
        return {"overview": "", "timeline": {"stages": []}}

    try:
        return _do_analyze(entries, target_name, api_key, api_base)
    except Exception:
        return _fallback(entries)


def _build_timeline_entries(chunk_results: list[dict], chunks: list[dict]) -> list[dict]:
    chunk_meta = {c["index"]: c for c in chunks}
    entries: list[dict] = []
    for result in chunk_results:
        if not result or result.get("error"):
            continue
        meta = chunk_meta.get(result.get("chunk_index"), {})
        relationship_facts = [
            frag.get("fact", "").strip()
            for frag in result.get("relationship_fragments", [])
            if frag.get("fact", "").strip()
        ]
        entry = {
            "chat_day": meta.get("chat_day") or meta.get("time_start", "")[:10],
            "time_start": meta.get("time_start", ""),
            "time_end": meta.get("time_end", ""),
            "start_msg_index": meta.get("start_msg_index", 0),
            "end_msg_index": meta.get("end_msg_index", 0),
            "summary": result.get("chunk_summary", ""),
            "key_events": result.get("key_events", [])[:8],
            "relationship_facts": relationship_facts[:8],
            "topics": result.get("topics", [])[:6],
        }
        if entry["summary"] or entry["key_events"] or entry["relationship_facts"]:
            entries.append(entry)
    entries.sort(key=lambda e: (e["start_msg_index"], e["time_start"]))
    return entries


def _do_analyze(entries: list[dict], target_name: str, api_key: str, api_base: str) -> dict:
    """Month → year → global reduce so every analysis chunk participates."""
    day_entries = _build_day_entries(entries, target_name, api_key, api_base)
    month_entries = _build_month_entries(day_entries, target_name, api_key, api_base)
    if len(month_entries) <= 12:
        result = _do_analyze_direct(month_entries, target_name, api_key, api_base)
        result["day_summaries"] = {entry["chat_day"]: entry for entry in day_entries}
        return result

    years: dict[str, list[dict]] = {}
    for entry in month_entries:
        years.setdefault(entry["time_start"][:4] or "unknown", []).append(entry)
    year_entries: list[dict] = []
    for year, items in sorted(years.items()):
        result = _do_analyze_direct(items, target_name, api_key, api_base)
        timeline = result.get("timeline", {})
        year_entries.append({
            "time_start": year,
            "time_end": year,
            "start_msg_index": items[0].get("start_msg_index", 0),
            "end_msg_index": items[-1].get("end_msg_index", 0),
            "summary": result.get("overview", ""),
            "key_events": [event for stage in timeline.get("stages", []) for event in stage.get("key_events", [])][:20],
            "relationship_facts": timeline.get("repair_patterns", []) + timeline.get("sensitive_points", []),
            "topics": [],
        })
    result = _do_analyze_direct(year_entries, target_name, api_key, api_base)
    result["day_summaries"] = {entry["chat_day"]: entry for entry in day_entries}
    return result


def _build_day_entries(
    entries: list[dict], target_name: str, api_key: str, api_base: str
) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for entry in entries:
        groups.setdefault(entry.get("chat_day") or entry.get("time_start", "")[:10], []).append(entry)
    reduced_by_day = {
        day: _aggregate_period_entry(day, items)
        for day, items in groups.items()
        if len(items) == 1
    }
    busy_days = [(day, items) for day, items in groups.items() if len(items) > 1]
    if busy_days:
        with ThreadPoolExecutor(max_workers=min(REDUCE_MAX_WORKERS, len(busy_days))) as executor:
            futures = {
                executor.submit(
                    _reduce_period_entry, day, items, target_name, api_key, api_base
                ): day
                for day, items in busy_days
            }
            for future in as_completed(futures):
                reduced_by_day[futures[future]] = future.result()
    result = []
    for day in sorted(groups):
        reduced = reduced_by_day[day]
        reduced["chat_day"] = day
        result.append(reduced)
    return result


def _build_month_entries(
    entries: list[dict], target_name: str, api_key: str, api_base: str
) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for entry in entries:
        month = str(entry.get("time_start", ""))[:7] or "unknown"
        groups.setdefault(month, []).append(entry)
    reduced_by_month = {}
    busy_months = []
    for month, items in groups.items():
        serialized_length = len(json.dumps(items, ensure_ascii=False))
        if len(items) > 10 or serialized_length > 12000:
            busy_months.append((month, items))
        else:
            reduced_by_month[month] = _aggregate_period_entry(month, items)
    if busy_months:
        with ThreadPoolExecutor(max_workers=min(REDUCE_MAX_WORKERS, len(busy_months))) as executor:
            futures = {
                executor.submit(
                    _reduce_period_entry, month, items, target_name, api_key, api_base
                ): month
                for month, items in busy_months
            }
            for future in as_completed(futures):
                reduced_by_month[futures[future]] = future.result()
    return [reduced_by_month[month] for month in sorted(groups)]


def _aggregate_period_entry(label: str, items: list[dict]) -> dict:
    return {
        "time_start": label,
        "time_end": label,
        "start_msg_index": min(item.get("start_msg_index", 0) for item in items),
        "end_msg_index": max(item.get("end_msg_index", 0) for item in items),
        "summary": "；".join(item.get("summary", "") for item in items if item.get("summary")),
        "key_events": list(dict.fromkeys(event for item in items for event in item.get("key_events", []))),
        "relationship_facts": list(dict.fromkeys(fact for item in items for fact in item.get("relationship_facts", []))),
        "topics": list(dict.fromkeys(topic for item in items for topic in item.get("topics", []))),
    }


def _reduce_period_entry(
    label: str, items: list[dict], target_name: str, api_key: str, api_base: str
) -> dict:
    """LLM-reduce a busy day/month without dropping later sub-chunks."""
    client = _get_client(api_key, api_base)
    compact = json.dumps(items, ensure_ascii=False)
    prompt = f"""将「{target_name}」和用户在 {label} 的以下局部聊天分析合并成一个时间段摘要。

局部分析：{compact}

输出纯 JSON：
{{"summary":"完整但精炼的时间段摘要", "key_events":[], "relationship_facts":[], "topics":[]}}

要求：合并重复内容；保留时间、人物、地点、约定、冲突和关系变化；不得编造；不要因为内容多而只保留开头。"""
    try:
        response = client.chat.completions.create(
            model=llm_model(), messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=3000, response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
        )
        parsed = json.loads((response.choices[0].message.content or "{}").strip())
        aggregate = _aggregate_period_entry(label, items)
        aggregate["summary"] = str(parsed.get("summary", aggregate["summary"]))[:2000]
        aggregate["key_events"] = _ensure_list(parsed.get("key_events"))[:30]
        aggregate["relationship_facts"] = _ensure_list(parsed.get("relationship_facts"))[:30]
        aggregate["topics"] = _ensure_list(parsed.get("topics"))[:20]
        return aggregate
    except Exception:
        return _aggregate_period_entry(label, items)


def _do_analyze_direct(entries: list[dict], target_name: str, api_key: str, api_base: str) -> dict:
    client = _get_client(api_key, api_base)
    compact = json.dumps(entries, ensure_ascii=False)
    prompt = f"""你在为一个虚拟女友项目总结过去聊天记录中的关系演变。

已知「{target_name}」是女友/角色。下面是按时间顺序压缩后的聊天时间块，每块包含摘要、关键事件、关系事实和话题。

时间块 JSON：
{compact}

请输出纯 JSON，不要 markdown：
{{
  "overview": "用一段话总结两人的关系如何演变，以及目前最重要的关系特征",
  "current_state": "当前关系状态",
  "sensitive_points": ["关系中的敏感点"],
  "repair_patterns": ["冲突后的修复/和好模式"],
  "stages": [
    {{
      "label": "阶段名，如初识期/暧昧升温期/稳定期/磨合期",
      "time_range": "YYYY-MM-DD ~ YYYY-MM-DD",
      "start_msg_index": 0,
      "end_msg_index": 100,
      "summary": "这一阶段发生了什么",
      "relationship_state": "这一阶段的关系状态",
      "key_events": ["关键事件"],
      "interaction_patterns": ["相处模式"]
    }}
  ]
}}

规则：
- 按时间顺序总结，不要编造不存在的事件
- 阶段控制在 3-8 个
- key_events 和 interaction_patterns 只保留有证据的内容
- 不要把关系美化、戏剧化，也不要写成心理诊断
- "敏感点"必须来自多次冲突/反复提及；只有一次证据时写得保守
- "修复模式"必须说明谁通常先缓和、靠什么方式缓和；证据不足则留空
- current_state 只描述聊天记录末尾能支持的状态，不要推断现实关系结局
- 如果时间不完整，用消息范围辅助判断阶段"""

    trace_inputs = {
        "model": llm_model(),
        "target_name": target_name,
        "entries": entries,
        "messages": [{"role": "user", "content": prompt}],
    }
    record_trace(
        "preprocessing.relationship_overview.prompt",
        trace_inputs,
        metadata={"target_name": target_name, "entry_count": len(entries)},
    )
    resp = client.chat.completions.create(
        model=llm_model(),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=8000,
        response_format={"type": "json_object"},
        extra_body={"thinking": {"type": "disabled"}},
    )
    message = resp.choices[0].message
    content = (message.content or "").strip()
    if not content:
        raise ValueError(
            "LLM returned empty relationship overview"
            f"; finish_reason={resp.choices[0].finish_reason}"
        )
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
        if "```" in content:
            content = content.rsplit("```", 1)[0]
        content = content.strip()
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("relationship overview JSON must be object")
    overview = str(parsed.get("overview", ""))[:2000]
    timeline = {
        "stages": _ensure_stages(parsed.get("stages")),
        "current_state": str(parsed.get("current_state", ""))[:500],
        "sensitive_points": _ensure_list(parsed.get("sensitive_points"))[:10],
        "repair_patterns": _ensure_list(parsed.get("repair_patterns"))[:10],
    }
    result = {"overview": overview, "timeline": timeline}
    record_trace(
        "preprocessing.relationship_overview.output",
        trace_inputs,
        {"raw_content": content, "result": result},
        run_type="llm",
        metadata={"target_name": target_name, "entry_count": len(entries)},
    )
    return result


def _ensure_stages(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    stages = []
    for item in value:
        if not isinstance(item, dict):
            continue
        stages.append({
            "label": str(item.get("label", ""))[:100],
            "time_range": str(item.get("time_range", ""))[:100],
            "start_msg_index": _to_int(item.get("start_msg_index")),
            "end_msg_index": _to_int(item.get("end_msg_index")),
            "summary": str(item.get("summary", ""))[:500],
            "relationship_state": str(item.get("relationship_state", ""))[:300],
            "key_events": _ensure_list(item.get("key_events"))[:10],
            "interaction_patterns": _ensure_list(item.get("interaction_patterns"))[:10],
        })
    return stages[:8]


def _fallback(entries: list[dict]) -> dict:
    lines: list[str] = []
    for entry in entries:
        label = entry.get("time_start") or str(entry.get("start_msg_index", ""))
        summary = entry.get("summary", "")
        if summary:
            lines.append(f"- {label}: {summary}")
        for fact in entry.get("relationship_facts", [])[:2]:
            lines.append(f"- {label}: {fact}")
    overview = "从导入聊天记录中整理出的两人关系概览：\n" + "\n".join(lines[:40])
    timeline = {
        "stages": [{
            "label": "聊天记录整体阶段",
            "time_range": _range_label(entries),
            "start_msg_index": entries[0].get("start_msg_index", 0),
            "end_msg_index": entries[-1].get("end_msg_index", 0),
            "summary": "基于时间块摘要生成的轻量关系概览，AI Reduce 失败时使用。",
            "relationship_state": "",
            "key_events": [],
            "interaction_patterns": [],
        }],
        "current_state": "",
        "sensitive_points": [],
        "repair_patterns": [],
    }
    return {"overview": overview[:2000], "timeline": timeline}


def _range_label(entries: list[dict]) -> str:
    start = entries[0].get("time_start", "")
    end = entries[-1].get("time_end", "")
    if start or end:
        return f"{start} ~ {end}"
    return ""


def _ensure_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v)[:300] for v in value if str(v).strip()]


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
