"""AI provider configuration helpers.

The project uses three provider groups:
- DashScope for embeddings, ASR and TTS.
- A text LLM for every non-vision generative task.
- Zhipu/BigModel GLM only for image understanding in the final conversation.

LLM_* is the canonical, provider-neutral configuration for the text model;
any OpenAI-compatible provider works (the base URL selects the vendor).
DEEPSEEK_* remains a legacy fallback for existing setups.  VISION_LLM_*
configures the GLM vision route.
"""

import os


DEFAULT_DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"
DEFAULT_LLM_MODEL = "deepseek-v4-pro"
DEFAULT_DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def chat_api_key() -> str:
    """Compatibility alias for the final text conversation route."""
    return llm_api_key()


def chat_api_base() -> str:
    return llm_api_base()


def chat_model() -> str:
    return llm_model()


def llm_api_key() -> str:
    return os.getenv("LLM_API_KEY", "").strip() or os.getenv("DEEPSEEK_API_KEY", "").strip()


def llm_api_base() -> str:
    return (
        os.getenv("LLM_API_BASE", "").strip()
        or os.getenv("DEEPSEEK_API_BASE", "").strip()
        or DEFAULT_DEEPSEEK_API_BASE
    )


def llm_model() -> str:
    return (
        os.getenv("LLM_MODEL", "").strip()
        or os.getenv("DEEPSEEK_MODEL", "").strip()
        or DEFAULT_LLM_MODEL
    )


def vision_llm_api_key() -> str:
    return os.getenv("VISION_LLM_API_KEY", "").strip() or os.getenv("GLM_API_KEY", "").strip()


def vision_llm_api_base() -> str:
    return os.getenv("VISION_LLM_API_BASE", "").strip() or "https://open.bigmodel.cn/api/paas/v4"


def vision_llm_model() -> str:
    return os.getenv("VISION_LLM_MODEL", "glm-5v-turbo").strip() or "glm-5v-turbo"


# ── 超时 ──────────────────────────────────────────────────────────────────
# 分层的起点值，用实测校准。两条约束写在这里，免得后人乱调：
#
#   * turn_deadline 一响，用户这一轮就什么都没有（拿到的是一条错误提示），
#     所以它必须**大于**真实慢路径，只该在病态情况下响。
#   * 各分层值之和**大于** turn_deadline 是正常的、也是预期的：分层超时负责
#     「一处挂住 → 优雅降级（这轮少了情绪/少了记忆，但回复照出）」，deadline
#     负责「多处挂住 → 早死早超生」。两者不是冗余关系。
#
# 这些值是**单次尝试**的上限。请求路径上的 client 都显式设了 max_retries=1，
# 所以单点的真实最坏是 2×。openai SDK 默认是 max_retries=2（3×）——不算上
# 重试次数的超时预算全是假账。


def embedding_timeout() -> float:
    """Embedding 请求。短请求，但单轮最多 9 次（3 query × semantic/imported/online）。"""
    return float(os.getenv("LLM_TIMEOUT_EMBEDDING", "").strip() or 8)


def sub_llm_timeout() -> float:
    """图内辅助 LLM：情绪分析、检索规划、重排、压缩。输出都是短 JSON。"""
    return float(os.getenv("LLM_TIMEOUT_SUB", "").strip() or 12)


def reply_timeout() -> float:
    """最终回复。输出最长，给得最宽。"""
    return float(os.getenv("LLM_TIMEOUT_REPLY", "").strip() or 45)


def turn_deadline() -> float:
    """整轮上限，包住 app.ainvoke 整张图。"""
    return float(os.getenv("CHAT_TURN_DEADLINE", "").strip() or 90)


def summary_timeout() -> float:
    """会话摘要折叠，**单批**的上限。

    注意它比别的辅助 LLM 宽得多：摘要是要读一长段对话再写一长段摘要，本来就慢。
    批数不限，所以真正兜住总时长的是 conversation_summary 里的
    SUMMARY_FOLD_BUDGET_SECONDS，不是这个值。
    """
    return float(os.getenv("LLM_TIMEOUT_SUMMARY", "").strip() or 40)


def require_llm_config():
    missing = []
    if not llm_api_key():
        missing.append("LLM_API_KEY（或 DEEPSEEK_API_KEY）")
    if not llm_api_base():
        missing.append("LLM_API_BASE（或 DEEPSEEK_API_BASE）")
    if not llm_model():
        missing.append("LLM_MODEL（或 DEEPSEEK_MODEL）")
    if missing:
        raise RuntimeError(
            "缺少大模型配置: "
            + ", ".join(missing)
            + "。文本模型使用 LLM_*（OpenAI 兼容任意厂商）；"
            "不要使用阿里云 embedding/语音的 API Key。"
        )


def require_chat_config():
    """Compatibility alias for non-vision conversation validation."""
    require_llm_config()


def dashscope_api_key() -> str:
    return os.getenv("DASHSCOPE_API_KEY") or os.getenv("API_KEY", "")


def dashscope_api_base() -> str:
    return os.getenv("DASHSCOPE_API_BASE") or os.getenv("API_BASE") or DEFAULT_DASHSCOPE_BASE


def dashscope_wss_url() -> str:
    return os.getenv("DASHSCOPE_WSS_URL") or os.getenv("WSS_URL", "")


def dashscope_voice_url() -> str:
    return (
        os.getenv("DASHSCOPE_VOICE_URL")
        or os.getenv("VOICE_URL")
        or "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
    )
