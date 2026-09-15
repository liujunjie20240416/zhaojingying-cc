"""Build a compact always-on speaking style signature from character facts."""


def build_style_profile(facts: list[str]) -> str:
    transient = ("当天", "本次", "这段对话", "某次", "当晚")
    buckets = {
        "address": (2, ("称呼", "昵称")),
        "language": (4, ("说话", "语气", "口头禅", "叠词", "拟声词")),
        "emoji": (2, ("表情", "颜文字")),
        "interaction": (3, ("安慰", "撒娇", "生气", "鼓励", "关心")),
    }
    selected: list[str] = []
    normalized_seen: set[str] = set()
    for _, (limit, signals) in buckets.items():
        count = 0
        for raw_fact in facts:
            fact = str(raw_fact).strip().rstrip("。")
            if not fact or any(word in fact for word in transient):
                continue
            normalized = "".join(fact.split())
            if normalized in normalized_seen or not any(signal in fact for signal in signals):
                continue
            normalized_seen.add(normalized)
            selected.append(fact)
            count += 1
            if count >= limit:
                break
    return "\n".join(f"- {fact}" for fact in selected)[:1500]
