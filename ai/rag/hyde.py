from openai import OpenAI

from ai.config import llm_api_base, llm_api_key, llm_model, require_llm_config


class HyDEGenerator:
    """HyDE (Hypothetical Document Embeddings) — 用 LLM 生成假设文档，
    再用假设文档做向量检索，解决用户 query 与文档之间的语义 gap"""

    def __init__(self, api_key: str = "", api_base: str = ""):
        if not api_key and not api_base:
            require_llm_config()
        self.client = OpenAI(
            api_key=api_key or llm_api_key(),
            base_url=api_base or llm_api_base(),
        )

    def generate(self, query: str) -> str:
        """根据用户查询生成假设性回答文档 (100-200字)"""
        prompt = f"""根据用户的查询，模拟写一段可能的聊天记录片段（100-200字）。
这段文字将被用于语义检索，因此请尽可能贴近真实聊天记录的写作风格：
- 第一人称对话
- 自然口语化
- 包含具体细节

用户查询："{query}"

假设的聊天记录片段："""

        resp = self.client.chat.completions.create(
            model=llm_model(),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=300,
        )
        return resp.choices[0].message.content.strip()
