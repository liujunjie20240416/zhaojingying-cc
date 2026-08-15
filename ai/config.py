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
