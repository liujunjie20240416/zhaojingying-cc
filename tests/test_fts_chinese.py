"""FTS5 中文检索回归测试：tokens 列存 jieba 分词，使中文关键词 MATCH 生效。

原始 bug：FTS5 表用 unicode61 tokenizer 不切中文，整段中文是一个 token，
MATCH "生日" 永远为 0；LIKE 兜底无排序只捞最早两行，真实对话进不了结果。
修复：建表加 tokens 列，导入时用 jieba 分词（空格连接），查询侧保持表级 MATCH。
"""
import pytest


@pytest.fixture
def fts_character():
    from django.contrib.auth.models import User
    from web.models.character import Character
    from web.models.user import UserProfile

    profile = UserProfile.objects.create(user=User.objects.create_user(username="fts-cn"))
    return Character.objects.create(
        author=profile, name="女友", profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )


@pytest.mark.django_db
def test_ensure_fts5_table_has_tokens_column(fts_character):
    """建表必须带 tokens 列，且 tokens 列能被中文 MATCH 命中"""
    from django.db import connection
    from api.import_data import _ensure_fts5_table, _tokenize_chinese

    table = _ensure_fts5_table(fts_character.id)
    # tokens 用 jieba 真实切词（"生日当天"切出独立"生日"token）
    content = "这周过生日，生日当天还要上课"
    tokens = _tokenize_chinese(content)
    assert "生日" in tokens.split()
    with connection.cursor() as c:
        c.execute(
            f'INSERT INTO "{table}" (sender, content, timestamp, tokens) '
            "VALUES ('用户', ?, '2024-02-01', ?)",
            [content, tokens],
        )
        # content 原文在 unicode61 下不可命中，tokens 分词列可以
        c.execute(f'SELECT content FROM "{table}" WHERE "{table}" MATCH %s', ["生日"])
        assert c.fetchone() == (content,)


@pytest.mark.django_db
def test_sync_fts5_populates_jieba_tokens_and_matches(fts_character):
    """导入同步后 tokens 列是 jieba 分词结果，多词 AND 能精确定位"""
    from django.db import connection
    from web.models.chat_message import ChatMessage
    from api.import_data import _sync_fts5_table

    ChatMessage.objects.create(
        character=fts_character, sender="用户",
        content="这周过生日，生日当天还要上课", timestamp="2024-02-01", msg_index=0,
    )
    ChatMessage.objects.create(
        character=fts_character, sender="用户",
        content="室友生日给她买蛋糕", timestamp="2024-02-02", msg_index=1,
    )
    table = _sync_fts5_table(fts_character.id)

    with connection.cursor() as c:
        c.execute(
            f'SELECT content FROM "{table}" WHERE "{table}" MATCH %s',
            ["生日 AND 上课"],
        )
        rows = [row[0] for row in c.fetchall()]
        assert rows == ["这周过生日，生日当天还要上课"]


@pytest.mark.django_db
def test_fts5_search_recovers_birthday_conversation(fts_character):
    """端到端回归：原始 bug 场景——查'你记得我的生日吗'必须命中真实生日对话"""
    from web.models.chat_message import ChatMessage
    from ai.rag.retriever import HybridRetriever

    ChatMessage.objects.create(
        character=fts_character, sender="用户",
        content="这周过生日，生日当天还要上课", timestamp="2024-02-01", msg_index=0,
    )
    ChatMessage.objects.create(
        character=fts_character, sender="AI",
        content="那就三号过生日，生日快乐", timestamp="2024-02-01", msg_index=1,
    )
    ChatMessage.objects.create(
        character=fts_character, sender="用户",
        content="室友生日给她买蛋糕", timestamp="2024-02-02", msg_index=2,
    )
    from api.import_data import _sync_fts5_table
    _sync_fts5_table(fts_character.id)

    results = HybridRetriever(api_key="", api_base="").fts5_search(
        "你记得我的生日吗", fts_character.id, limit=10
    )
    contents = [r["content"] for r in results]
    assert any("这周过生日" in content for content in contents), contents
