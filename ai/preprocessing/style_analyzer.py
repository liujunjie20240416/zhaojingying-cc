"""Compile raw character messages and extracted facts into a bounded style guide."""

import json
import statistics

from openai import OpenAI

from ai.config import llm_api_base, llm_api_key, llm_model
from ai.memory.style import build_style_profile
from web.models.chat_message import ChatMessage
from web.models.memory import SemanticMemory

STYLE_FACT_SIGNALS = (
    "说话", "称呼", "语气", "口头禅", "表情", "颜文字",
    "叠词", "短句", "长句", "撒娇", "调侃", "安慰",
)


def analyze_style_profile(
    character_id: int, target_name: str, chunk_results: list[dict],
    api_key: str = "", api_base: str = "",
) -> str:
    dialogue = list(ChatMessage.objects.filter(
        character_id=character_id,
    ).order_by("msg_index").values("sender", "content"))
    messages = [item["content"] for item in dialogue if item["sender"] == target_name]
    facts = [
        str(fragment.get("fact", "")).strip()
        for result in chunk_results if result and not result.get("error")
        for fragment in result.get("girlfriend_fragments", [])
        if str(fragment.get("fact", "")).strip()
        and any(
            signal in str(fragment.get("fact", ""))
            for signal in STYLE_FACT_SIGNALS
        )
    ]
    if not facts:
        facts = [fact for fact in dict.fromkeys(
            SemanticMemory.objects.filter(
                friend__character_id=character_id,
                subject="girlfriend",
                source="import",
                is_active=True,
            ).order_by("-confidence", "id").values_list("fact", flat=True)
        ) if any(signal in fact for signal in STYLE_FACT_SIGNALS)]
    fallback = build_style_profile(facts)
    if not messages:
        return fallback

    lengths = sorted(len(message or "") for message in messages)
    runs: list[int] = []
    current_run = 0
    for item in dialogue:
        if item["sender"] == target_name:
            current_run += 1
        elif current_run:
            runs.append(current_run)
            current_run = 0
    if current_run:
        runs.append(current_run)
    sorted_runs = sorted(runs) or [1]
    stats = {
        "message_count": len(messages),
        "median_chars": statistics.median(lengths),
        "p75_chars": lengths[int(len(lengths) * .75)],
        "p90_chars": lengths[int(len(lengths) * .9)],
        "short_le_10_pct": round(sum(length <= 10 for length in lengths) / len(lengths) * 100, 1),
        "consecutive_run_median": statistics.median(sorted_runs),
        "consecutive_run_p75": sorted_runs[int(len(sorted_runs) * .75)],
        "runs_ge_2_pct": round(sum(run >= 2 for run in sorted_runs) / len(sorted_runs) * 100, 1),
    }
    # Evenly distributed examples avoid learning only the beginning/end of the import.
    step = max(1, len(messages) // 120)
    examples = [message for message in messages[::step][:120] if message][:120]
    prompt = f"""根据真实微信消息统计和候选事实，编译女友角色「{target_name}」的回复风格。

消息统计：{json.dumps(stats, ensure_ascii=False)}
分布式样例：{json.dumps(examples, ensure_ascii=False)}
候选风格事实：{json.dumps(facts[:200], ensure_ascii=False)}

输出纯JSON：
{{
  "addressing": "称呼习惯",
  "tone": "整体语气",
  "diction": "常用语气词、叠词和口语",
  "emoji": "表情使用习惯",
  "interaction": "安慰、撒娇、调侃等互动方式",
  "verbosity": "单条消息长度和是否避免长篇",
  "bubble_pattern": "一次回复通常拆成几条消息",
  "avoid": ["不符合角色的表达"]
}}

规则：严格依据导入聊天样例和全量统计；合并同义描述；不要罗列几百个例子；不要虚构心理；
描述一次回复通常适合几个独立气泡，但不要把普通换行当成气泡分隔。"""
    try:
        client = OpenAI(api_key=api_key or llm_api_key(), base_url=api_base or llm_api_base())
        response = client.chat.completions.create(
            model=llm_model(), messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=1200, response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
        )
        data = json.loads((response.choices[0].message.content or "{}").strip())
        labels = {
            "addressing": "称呼", "tone": "语气", "diction": "用词",
            "emoji": "表情", "interaction": "互动", "verbosity": "长度",
            "bubble_pattern": "消息节奏",
        }
        lines = [f"- {label}：{str(data.get(key, '')).strip()}" for key, label in labels.items() if data.get(key)]
        avoid = data.get("avoid") if isinstance(data.get("avoid"), list) else []
        if avoid:
            lines.append("- 避免：" + "；".join(str(item) for item in avoid[:6]))
        default_bubbles = "2-3" if stats["runs_ge_2_pct"] >= 50 else "1-2"
        lines.append(
            f"- 统计约束：历史单条消息中位数{stats['median_chars']}字，90%不超过{stats['p90_chars']}字；"
            f"普通闲聊默认建议{default_bubbles}个结构化气泡；气泡内部允许换行，完整解释或Markdown保持一个气泡。"
        )
        return "\n".join(lines)[:2000]
    except Exception:
        default_bubbles = "2-3" if stats["runs_ge_2_pct"] >= 50 else "1-2"
        return (
            fallback + "\n"
            f"- 消息节奏：历史单条中位数{stats['median_chars']}字，90%不超过{stats['p90_chars']}字；"
            f"普通闲聊默认建议{default_bubbles}个结构化气泡；气泡内部允许换行，完整解释保持一个气泡。"
        )[:2000]
