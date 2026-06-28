"""Lightweight memory intent detection.

先用规则实现，避免每轮聊天多一次 LLM 调用。输出用于决定检索优先级：
- target_subject: user / girlfriend / relationship / mixed
- time_mode: current / historical / early / recent / specific_time / any
- category_hint: identity / preference / experience / relationship / any
- needs_raw_chat: 是否强依赖原始聊天证据
"""

import re


def detect_memory_intent(user_msg: str) -> dict:
    text = user_msg or ""
    target_subject = _detect_subject(text)
    time_mode = _detect_time_mode(text)
    category_hint = _detect_category(text)
    needs_raw_chat = _needs_raw_chat(text, time_mode)
    return {
        "target_subject": target_subject,
        "time_mode": time_mode,
        "category_hint": category_hint,
        "needs_raw_chat": needs_raw_chat,
    }


def _detect_subject(text: str) -> str:
    relationship_words = ["我们", "两个人", "两人", "关系", "相处", "吵架", "和好", "约定", "刚认识"]
    girlfriend_words = ["你以前", "你那时候", "你的性格", "你喜欢", "你是不是", "女友", "她", "说话方式", "撒娇"]
    user_words = ["我喜欢", "我讨厌", "我是不是", "我以前", "我现在", "我的", "我能不能", "我还"]
    if any(word in text for word in relationship_words):
        return "relationship"
    if any(word in text for word in girlfriend_words):
        return "girlfriend"
    if any(word in text for word in user_words):
        return "user"
    return "mixed"


def _detect_time_mode(text: str) -> str:
    if re.search(r"\d{4}年|\d{1,2}月|\d{1,2}号|\d{4}-\d{1,2}", text):
        return "specific_time"
    if any(word in text for word in ["刚认识", "最开始", "第一次", "刚加", "初识"]):
        return "early"
    if any(word in text for word in ["以前", "那时候", "当时", "之前", "曾经", "过去", "上次"]):
        return "historical"
    if any(word in text for word in ["现在", "最近", "目前", "这几天", "如今"]):
        return "current"
    if any(word in text for word in ["后来", "最后", "现在关系"]):
        return "recent"
    return "any"


def _detect_category(text: str) -> str:
    if any(word in text for word in ["喜欢", "讨厌", "口味", "吃", "喝", "爱吃", "偏好", "习惯"]):
        return "preference"
    if any(word in text for word in ["生日", "名字", "在哪", "工作", "学校", "城市", "身份", "职业"]):
        return "identity"
    if any(word in text for word in ["发生", "经历", "记得", "那次", "第一次", "去过", "做过"]):
        return "experience"
    if any(word in text for word in ["关系", "相处", "吵架", "和好", "哄", "冷处理", "安全感", "怎么对我"]):
        return "relationship"
    return "any"


def _needs_raw_chat(text: str, time_mode: str) -> bool:
    recall_words = ["记得", "原话", "说过", "聊过", "那次", "什么时候", "哪天"]
    return time_mode in {"historical", "early", "specific_time"} or any(word in text for word in recall_words)
