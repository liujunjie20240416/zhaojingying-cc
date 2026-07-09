# ai/rag/reranker.py
"""LLM Reranker — 用 API 对检索候选集打分重排序，零本地模型依赖。"""

import json
from openai import OpenAI

from ai.config import llm_api_base, llm_api_key, llm_model, require_llm_config
from ai.tracing import record_trace


class Reranker:
    """用 LLM 对候选文档做相关性打分 (0-1)，取 top_k。

    与项目风格一致：所有 AI 能力走 API，无本地模型。
    """

    def __init__(self, api_key: str = "", api_base: str = ""):
        if not api_key and not api_base:
            require_llm_config()
        self.client = OpenAI(
            api_key=api_key or llm_api_key(),
            base_url=api_base or llm_api_base(),
        )

    def rerank(self, query: str, docs: list[dict], top_k: int = 5) -> list[dict]:
        """对 docs 重排序，返回 top_k。docs: [{"content": str, "score": float, ...}]"""
        if not docs:
            return []

        # 少量文档直接按原分数排序，无需 LLM
        if len(docs) <= top_k:
            docs.sort(key=lambda d: d.get("score", 0), reverse=True)
            return docs

        # 构建 prompt：让 LLM 对每个候选文档打分
        candidates = "\n\n".join(
            f"[{i}] {doc['content'][:300]}" for i, doc in enumerate(docs)
        )

        prompt = f"""对以下候选文档与用户查询的相关性打分（0-1），输出纯JSON数组。

查询："{query}"

候选文档：
{candidates}

输出格式：[[0, 0.95], [1, 0.3], [2, 0.8]] — 每个元素为 [文档编号, 相关性分数]

规则：
- 直接回答查询的文档 > 间接相关的文档 > 无关文档
- 包含具体事实的文档加分
- 只输出JSON数组，不要任何其他文字"""

        trace_inputs = {
            "model": llm_model(),
            "query": query,
            "docs": docs,
            "top_k": top_k,
            "messages": [{"role": "user", "content": prompt}],
        }
        record_trace("rag.reranker.prompt", trace_inputs)
        try:
            resp = self.client.chat.completions.create(
                model=llm_model(),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=1000,
            )
            content = resp.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
                if "```" in content:
                    content = content.rsplit("```", 1)[0]
                content = content.strip()

            scores = json.loads(content)
            if isinstance(scores, list):
                for item in scores:
                    if isinstance(item, list) and len(item) == 2:
                        idx, score = item
                        if isinstance(idx, int) and 0 <= idx < len(docs):
                            docs[idx]["score"] = float(score)
            record_trace(
                "rag.reranker.output",
                trace_inputs,
                {"raw_content": content, "scores": scores, "reranked_docs": docs[:top_k]},
                run_type="llm",
            )
        except Exception:
            record_trace(
                "rag.reranker.output",
                trace_inputs,
                {"reranked_docs": docs[:top_k], "error": "rerank_failed"},
                run_type="llm",
            )
            pass  # LLM 失败时保持原分数

        docs.sort(key=lambda d: d.get("score", 0), reverse=True)
        return docs[:top_k]
