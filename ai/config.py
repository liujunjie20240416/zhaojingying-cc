"""AI provider configuration helpers.

The project uses two different providers:
- DashScope for embeddings, ASR and TTS.
- A chat LLM for preprocessing, memory reflection and conversation.

Legacy API_KEY/API_BASE remain as fallbacks for DashScope only. LLM calls must
use explicit LLM_* variables so an embedding/voice key is never accidentally
used for preprocessing or chat.
"""

import os


DEFAULT_LLM_MODEL = "deepseek-v4-pro"
DEFAULT_DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def llm_api_key() -> str:
    return os.getenv("LLM_API_KEY", "").strip()


def llm_api_base() -> str:
    return os.getenv("LLM_API_BASE", "").strip()


def llm_model() -> str:
    return os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL).strip() or DEFAULT_LLM_MODEL


def require_llm_config():
    missing = []
    if not llm_api_key():
        missing.append("LLM_API_KEY")
    if not llm_api_base():
        missing.append("LLM_API_BASE")
    if not llm_model():
        missing.append("LLM_MODEL")
    if missing:
        raise RuntimeError(
            "缺少大模型配置: "
            + ", ".join(missing)
            + "。预处理、记忆反思和 AI 对话需要单独配置 LLM_*，不要使用阿里云 embedding/语音的 API。"
        )


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
