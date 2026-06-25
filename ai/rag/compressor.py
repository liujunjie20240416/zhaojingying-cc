# ai/rag/compressor.py
import os
from openai import OpenAI


class ContextCompressor:
    """LLM 上下文压缩 — 长检索结果压缩为精炼摘要，保留关键事实"""

    def __init__(self, api_key: str = "", api_base: str = ""):
        self.client = OpenAI(
            api_key=api_key or os.getenv("API_KEY"),
            base_url=api_base or os.getenv("API_BASE"),
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

        resp = self.client.chat.completions.create(
            model="deepseek-v4-pro",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=600,
        )
        return resp.choices[0].message.content.strip()
