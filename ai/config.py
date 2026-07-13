"""AI provider configuration helpers.

The project uses two provider groups:
- DashScope for embeddings, ASR and TTS.
- Zhipu/BigModel GLM for every generative LLM task.

GLM_* is the canonical text-model configuration. VISION_LLM_* can override the
vision route. Legacy LLM_API_KEY is accepted only as a key migration fallback;
legacy LLM base/model values deliberately cannot route traffic back to another
provider.
"""

import os


DEFAULT_GLM_API_BASE = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_LLM_MODEL = "glm-5.2"
DEFAULT_DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def llm_api_key() -> str:
    return (
        os.getenv("GLM_API_KEY", "").strip()
        or os.getenv("VISION_LLM_API_KEY", "").strip()
        or os.getenv("LLM_API_KEY", "").strip()
    )


def llm_api_base() -> str:
    return os.getenv("GLM_API_BASE", "").strip() or DEFAULT_GLM_API_BASE


def llm_model() -> str:
    return os.getenv("GLM_MODEL", DEFAULT_LLM_MODEL).strip() or DEFAULT_LLM_MODEL


def vision_llm_api_key() -> str:
    return os.getenv("VISION_LLM_API_KEY", "").strip() or llm_api_key()


def vision_llm_api_base() -> str:
    return os.getenv("VISION_LLM_API_BASE", "").strip() or llm_api_base()


def vision_llm_model() -> str:
    return os.getenv("VISION_LLM_MODEL", "glm-5v-turbo").strip() or "glm-5v-turbo"


def require_llm_config():
    missing = []
    if not llm_api_key():
        missing.append("GLM_API_KEY（也可暂时复用 VISION_LLM_API_KEY）")
    if not llm_api_base():
        missing.append("GLM_API_BASE")
    if not llm_model():
        missing.append("GLM_MODEL")
    if missing:
        raise RuntimeError(
            "缺少大模型配置: "
            + ", ".join(missing)
            + "。预处理、记忆反思和 AI 对话统一使用 GLM；不要使用阿里云 embedding/语音的 API Key。"
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
