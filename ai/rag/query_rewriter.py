import json
from openai import OpenAI

from ai.config import llm_api_base, llm_api_key, llm_model, require_llm_config
from ai.tracing import record_trace


class QueryRewriter:
    """将用户原始查询改写为 2-3 个不同角度的变体，提升检索召回率"""

    def __init__(self, api_key: str = "", api_base: str = ""):
        if not api_key and not api_base:
            require_llm_config()
        self.client = OpenAI(
            api_key=api_key or llm_api_key(),
            base_url=api_base or llm_api_base(),
        )

    def rewrite(self, query: str) -> list[str]:
        """返回改写后的查询列表（包含原始 query）"""
        prompt = f"""你是查询改写助手。用户说了一句中文口语，请从不同角度改写成2-3个检索查询。

规则：
- 将口语化表达具体化（"上次那事"→推测具体指什么）
- 补充可能的同义表达
- 输出纯JSON数组，不含任何其他文字

用户原话："{query}"

输出格式示例：["查询角度1", "查询角度2", "查询角度3"]"""

        trace_inputs = {
            "model": llm_model(),
            "query": query,
            "messages": [{"role": "user", "content": prompt}],
        }
        record_trace("rag.query_rewriter.prompt", trace_inputs)
        resp = self.client.chat.completions.create(
            model=llm_model(),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=200,
        )
        content = resp.choices[0].message.content.strip()
        # 清理 markdown code fence
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
        try:
            rewrites = json.loads(content)
            if not isinstance(rewrites, list):
                return [query]
            # 确保原始 query 在列表中
            rewrites = [q for q in rewrites if isinstance(q, str) and q.strip()]
            if query not in rewrites:
                rewrites.insert(0, query)
            result = rewrites[:4]
            record_trace(
                "rag.query_rewriter.output",
                trace_inputs,
                {"raw_content": content, "rewrites": result},
                run_type="llm",
            )
            return result
        except json.JSONDecodeError:
            record_trace(
                "rag.query_rewriter.output",
                trace_inputs,
                {"raw_content": content, "rewrites": [query], "error": "JSONDecodeError"},
                run_type="llm",
            )
            return [query]
