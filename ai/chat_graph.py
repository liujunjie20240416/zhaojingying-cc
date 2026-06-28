import re
from time import localtime
from typing import TypedDict, Annotated, Sequence

import lancedb
from django.db import connection
from django.utils.timezone import now, localtime
from lancedb import query
from langchain_community.vectorstores import LanceDB
from langchain_core.messages import BaseMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.constants import START, END
from langgraph.graph import add_messages, StateGraph
from langgraph.prebuilt import ToolNode

from pathlib import Path as _Path

from ai.config import llm_api_base, llm_api_key, llm_model, require_llm_config
from ai.custom_embeddings import CustomEmbeddings

_STORAGE_DIR = str(_Path(__file__).resolve().parent / "documents" / "lancedb_storage")


class ChatGraph:
    @staticmethod
    def create_app(character_id: int | None = None, character_name: str = "", chat_sender_name: str = ""):
        """创建聊天 Graph。character_id 用于查找对应的微信聊天记录向量表。"""

        @tool
        def get_time() -> str:
            """当需要查询精确时间时，调用此函数。返回格式为：[年-月-日 时:分:秒]"""
            return localtime(now()).strftime("%Y-%m-%d %H:%M:%S")

        @tool
        def search_knowledge_base(query: str) -> str:
            """当用户查询阿里云百炼平台的相关信息时，调用此函数。输入为要查询的问题，输出为查询结果。"""
            db = lancedb.connect(_STORAGE_DIR)
            embeddings = CustomEmbeddings()
            vector_db = LanceDB(
                connection=db,
                embedding=embeddings,
                table_name="my_knowledge_base",
            )
            docs = vector_db.similarity_search(query, k=3)
            context = "\n\n".join(
                [f"内容片段:{i + 1}\n{doc.page_content}" for i, doc in enumerate(docs)]
            )
            return f"从知识库中找到一下相关信息:\n\n{context}\n"

        @tool
        def search_wechat(query: str) -> str:
            """
当用户正在与特定角色（女友/前女友）对话时，调用此工具。

使用场景：
- 用户希望模拟特定人物的说话风格、语气或记忆
- 用户询问'我们以前的事'、'我的喜好'或进行深度情感互动
- 用户提到具体关键词（人名、地点、事件），需要精确查找

功能说明：
- 先从 FTS5 全文索引精确匹配关键词
- 再用 LanceDB 向量库做语义相似搜索
- 两者合并返回最相关的内容

使用规则：
- 不要直接逐字复述检索结果
- 应基于检索内容进行人格模仿式重写
- 回答必须符合角色的语气（自然、温柔、带情绪）
- 回答应像真实聊天，而不是知识库摘要

输出要求：
- 不要使用括号描写动作
- 不要写心理活动或舞台说明
- 只输出自然对话内容
            """
            if not character_id:
                return "暂无聊天记录数据。"

            lance_table = f"wechat_{character_id}"
            fts_table = f"chat_fts_{character_id}"
            db = lancedb.connect(_STORAGE_DIR)

            # 检查数据是否存在
            if lance_table not in db.table_names():
                return "暂无聊天记录数据，请先导入。"

            # ── 第1步：FTS5 关键词精确搜索 ──
            fts_results: list[str] = []
            with connection.cursor() as c:
                c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=%s",
                    [fts_table],
                )
                if c.fetchone():
                    # 用 jieba 分词提取关键词
                    import jieba

                    keywords: list[str] = []
                    for word in jieba.cut(query):
                        word = word.strip()
                        if len(word) >= 2 and re.search(r"[\u4e00-\u9fff]", word):
                            keywords.append(word)
                    keywords.extend(re.findall(r"[a-zA-Z]{3,}", query))
                    keywords = list(dict.fromkeys(sorted(keywords, key=len)))
                    if keywords:
                        safe_query = " OR ".join(
                            f'"{kw}"' for kw in keywords
                        )
                        try:
                            c.execute(
                                f'SELECT sender, timestamp, content FROM "{fts_table}" '
                                f'WHERE "{fts_table}" MATCH %s ORDER BY rank LIMIT 10',
                                [safe_query],
                            )
                            for row in c.fetchall():
                                fts_results.append(f"[{row[1]}] {row[0]}: {row[2]}")
                        except Exception:
                            pass

                    # 同时跑 LIKE 兜底（无论 FTS5 有无结果，补充精确匹配）
                    for kw in keywords:
                        try:
                            c.execute(
                                f'SELECT sender, timestamp, content FROM "{fts_table}" '
                                f'WHERE content LIKE %s LIMIT 2',
                                [f"%{kw}%"],
                            )
                            for row in c.fetchall():
                                line = f"[{row[1]}] {row[0]}: {row[2]}"
                                if line not in fts_results:
                                    fts_results.append(line)
                        except Exception:
                            pass

            # ── 第2步：LanceDB 语义向量搜索 ──
            embeddings = CustomEmbeddings()
            vector_db = LanceDB(
                connection=db, embedding=embeddings, table_name=lance_table
            )
            docs = vector_db.similarity_search(query, k=5)
            semantic_results = [
                f"【语义匹配 {i + 1}】\n{doc.page_content[:600]}"
                for i, doc in enumerate(docs)
            ]

            # ── 合并结果 ──
            parts: list[str] = []
            if fts_results:
                parts.append("## 关键词精确匹配\n" + "\n\n".join(fts_results[:8]))
            if semantic_results:
                parts.append("## 语义相似匹配\n" + "\n\n".join(semantic_results[:3]))

            if not parts:
                return "未找到相关聊天记录。"

            # ── 动态检测发送人映射 ──
            all_senders: set[str] = set()
            for line in fts_results:
                m = re.match(r"\[.*?\]\s+(\S+?):", line)
                if m:
                    all_senders.add(m.group(1))
            # 也检查语义结果中的发送人
            for text in semantic_results:
                for m in re.finditer(r"\]\s+(\S+?):", text):
                    all_senders.add(m.group(1))

            sender_hint = ""
            if chat_sender_name:
                # chat_sender_name 就是导入时填的对方名字，即 AI 角色在聊天记录中的发送人
                # 另一个发送人就是用户
                others = all_senders - {chat_sender_name}
                other_str = "、".join(sorted(others)[:2]) if others else "对方"
                sender_hint = (
                    f"【重要】「{chat_sender_name}」=我（AI角色的话），"
                    f"「{other_str}」=你（对方的话）。\n"
                )

            return (
                f"以下是{character_name}的真实聊天记录：\n"
                + (sender_hint or "")
                + "\n\n".join(parts)
                + "\n"
            )

        tools = [get_time, search_knowledge_base, search_wechat]

        require_llm_config()
        llm = ChatOpenAI(
            model=llm_model(),
            openai_api_key=llm_api_key(),
            openai_api_base=llm_api_base(),
            streaming=True,
            model_kwargs={
                "stream_options": {
                    "include_usage": True,
                }
            },
        ).bind_tools(tools)

        class AgentState(TypedDict):
            messages: Annotated[Sequence[BaseMessage], add_messages]

        def model_call(state: AgentState) -> AgentState:
            from pprint import pprint

            pprint(state["messages"])
            res = llm.invoke(state["messages"])
            return {"messages": [res]}

        def should_continue(state: AgentState) -> str:
            last_message = state["messages"][-1]
            if last_message.tool_calls:
                return "tools"
            return "end"

        tool_node = ToolNode(tools)

        graph = StateGraph(AgentState)
        graph.add_node("agent", model_call)
        graph.add_node("tools", tool_node)

        graph.add_edge(START, "agent")
        graph.add_conditional_edges(
            "agent",
            should_continue,
            {
                "tools": "tools",
                "end": END,
            },
        )
        graph.add_edge("tools", "agent")

        return graph.compile()


# Backward compatibility: ChatGraph.create_app now delegates to SupervisorGraph
from ai.agents.supervisor_graph import create_supervisor_app

_original_create_app = ChatGraph.create_app


@staticmethod
def _compat_create_app(character_id=None, character_name="", chat_sender_name=""):
    return create_supervisor_app(
        character_id=character_id,
        character_name=character_name,
        character_profile="",
    )


ChatGraph.create_app = _compat_create_app
