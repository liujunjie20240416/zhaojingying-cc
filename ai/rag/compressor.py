# ai/rag/compressor.py
from openai import OpenAI

from ai.config import (
    llm_api_base,
    llm_api_key,
    llm_model,
    require_llm_config,
    sub_llm_timeout,
)
from ai.tracing import record_trace


class ContextCompressor:
    """LLM 上下文压缩 — 长检索结果压缩为精炼摘要，保留关键事实"""

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

    def compress(self, context: str, max_length: int = 500) -> str:
        """压缩上下文到 max_length 字符以内，保留事实细节"""
        if len(context) <= max_length:
            return context

        prompt = f"""压缩以下聊天记录片段为 {max_length} 字以内的精炼摘要。
规则：
- 保留所有具体事实（人名、地点、时间、偏好、承诺）
- 去掉客套话和重复内容
- 保留对话的时间顺序

原文：
{context[:3000]}

精炼摘要："""

        trace_inputs = {
            "model": llm_model(),
            "context": context,
            "max_length": max_length,
            "messages": [{"role": "user", "content": prompt}],
        }
        record_trace("rag.compressor.prompt", trace_inputs)
        resp = self.client.chat.completions.create(
            model=llm_model(),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=600,
        )
        result = resp.choices[0].message.content.strip()
        record_trace(
            "rag.compressor.output",
            trace_inputs,
            {"compressed_context": result},
            run_type="llm",
        )
        return result
