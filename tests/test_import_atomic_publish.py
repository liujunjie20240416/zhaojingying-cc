"""Regression coverage for atomic Imported Chat publication.

The live Imported Chat must remain queryable when preparing a replacement
fails.  The active version on Character is the publication pointer shared by
the SQLite FTS5 and LanceDB projections.
"""

import pytest
from django.contrib.auth.models import User
from django.db import connection


@pytest.fixture
def imported_character():
    from storage.models.character import Character
    from storage.models.user import UserProfile

    user = User.objects.create_user(username="atomic-import")
    profile = UserProfile.objects.create(user=user)
    return Character.objects.create(
        author=profile,
        name="女友",
        profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
        import_data_version="previous",
    )


@pytest.mark.django_db(transaction=True)
def test_failed_vector_build_preserves_live_imported_chat_and_fts(
    imported_character, monkeypatch,
):
    from api import import_data
    from storage.models.chat_message import ChatMessage

    old_message = ChatMessage.objects.create(
        character=imported_character,
        sender="用户",
        content="旧聊天必须保留",
        timestamp="2024-01-01 10:00:00",
        msg_index=0,
    )
    live_fts = import_data.imported_fts_table_name(
        imported_character.id, "previous"
    )
    import_data._sync_fts5_table(imported_character.id, table_name=live_fts)
    live_vector = import_data.imported_vector_table_name(
        imported_character.id, "previous"
    )

    class FakeDB:
        def __init__(self):
            self.tables = {live_vector}
            self.dropped = []

        def list_tables(self):
            return type("Listing", (), {"tables": list(self.tables)})()

        def drop_table(self, name):
            self.tables.remove(name)
            self.dropped.append(name)

    fake_db = FakeDB()
    monkeypatch.setattr(import_data.lancedb, "connect", lambda *_: fake_db)
    monkeypatch.setattr(
        import_data.LanceDB,
        "from_texts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("embedding failed")),
    )

    with pytest.raises(RuntimeError, match="embedding failed"):
        import_data.publish_imported_chat(
            imported_character,
            target_name="女友",
            messages=[{
                "sender": "女友",
                "content": "这次导入不应发布",
                "timestamp": "2025-01-01 10:00:00",
            }],
        )

    imported_character.refresh_from_db()
    assert imported_character.import_data_version == "previous"
    assert list(ChatMessage.objects.filter(character=imported_character).values_list("id", "content")) == [
        (old_message.id, "旧聊天必须保留")
    ]
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT content FROM "{live_fts}"')
        assert cursor.fetchall() == [("旧聊天必须保留",)]
    assert fake_db.tables == {live_vector}
    assert fake_db.dropped == []


@pytest.mark.django_db(transaction=True)
def test_failed_sqlite_publish_rolls_back_and_removes_unpublished_vector(
    imported_character, monkeypatch,
):
    from api import import_data
    from storage.models.character import Character
    from storage.models.chat_message import ChatMessage

    old_message = ChatMessage.objects.create(
        character=imported_character,
        sender="用户",
        content="旧聊天必须保留",
        timestamp="2024-01-01 10:00:00",
        msg_index=0,
    )
    live_fts = import_data.imported_fts_table_name(
        imported_character.id, "previous"
    )
    import_data._sync_fts5_table(imported_character.id, table_name=live_fts)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE %s",
            [f"chat_fts_{imported_character.id}%"],
        )
        fts_tables_before = {row[0] for row in cursor.fetchall()}
    live_vector = import_data.imported_vector_table_name(
        imported_character.id, "previous"
    )

    class FakeTable:
        def __init__(self, count):
            self.count = count

        def count_rows(self):
            return self.count

    class FakeDB:
        def __init__(self):
            self.tables = {live_vector: 1}

        def list_tables(self):
            return type("Listing", (), {"tables": list(self.tables)})()

        def open_table(self, name):
            return FakeTable(self.tables[name])

        def drop_table(self, name):
            del self.tables[name]

    fake_db = FakeDB()
    monkeypatch.setattr(import_data.lancedb, "connect", lambda *_: fake_db)

    def add_vector_table(_texts, *_args, **kwargs):
        kwargs["connection"].tables[kwargs["table_name"]] = len(_texts)

    monkeypatch.setattr(import_data.LanceDB, "from_texts", add_vector_table)
    original_save = Character.save

    def fail_pointer_publish(self, *args, **kwargs):
        if self.id == imported_character.id and self.import_data_version != "previous":
            raise RuntimeError("database write failed")
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(Character, "save", fail_pointer_publish)

    with pytest.raises(RuntimeError, match="database write failed"):
        import_data.publish_imported_chat(
            imported_character,
            target_name="女友",
            messages=[{
                "sender": "女友",
                "content": "这次导入也不应发布",
                "timestamp": "2025-01-01 10:00:00",
            }],
        )

    imported_character.refresh_from_db()
    assert imported_character.import_data_version == "previous"
    assert list(ChatMessage.objects.filter(character=imported_character).values_list("id", "content")) == [
        (old_message.id, "旧聊天必须保留")
    ]
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT content FROM "{live_fts}"')
        assert cursor.fetchall() == [("旧聊天必须保留",)]
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE %s",
            [f"chat_fts_{imported_character.id}%"],
        )
        assert {row[0] for row in cursor.fetchall()} == fts_tables_before
    assert fake_db.tables == {live_vector: 1}


@pytest.mark.django_db(transaction=True)
def test_successful_publish_switches_all_imported_chat_read_models(
    imported_character, monkeypatch,
):
    from api import import_data
    from storage.models.chat_message import ChatMessage

    ChatMessage.objects.create(
        character=imported_character,
        sender="用户",
        content="旧聊天",
        timestamp="2024-01-01 10:00:00",
        msg_index=0,
    )
    old_fts = import_data.imported_fts_table_name(imported_character.id, "previous")
    import_data._sync_fts5_table(imported_character.id, table_name=old_fts)
    old_vector = import_data.imported_vector_table_name(imported_character.id, "previous")

    class FakeTable:
        def __init__(self, count):
            self.count = count

        def count_rows(self):
            return self.count

    class FakeDB:
        def __init__(self):
            self.tables = {old_vector: 1}

        def list_tables(self):
            return type("Listing", (), {"tables": list(self.tables)})()

        def open_table(self, name):
            return FakeTable(self.tables[name])

        def drop_table(self, name):
            del self.tables[name]

    fake_db = FakeDB()
    monkeypatch.setattr(import_data.lancedb, "connect", lambda *_: fake_db)

    def add_vector_table(texts, *_args, **kwargs):
        kwargs["connection"].tables[kwargs["table_name"]] = len(texts)

    monkeypatch.setattr(import_data.LanceDB, "from_texts", add_vector_table)

    publication = import_data.publish_imported_chat(
        imported_character,
        target_name="女友",
        messages=[{
            "sender": "女友",
            "content": "新聊天已经完整发布",
            "timestamp": "2025-01-01 10:00:00",
        }],
    )

    imported_character.refresh_from_db()
    assert imported_character.import_data_version == publication["version"]
    assert list(ChatMessage.objects.filter(character=imported_character).values_list("content", flat=True)) == [
        "新聊天已经完整发布"
    ]
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT content FROM "{publication["fts_table"]}"')
        assert cursor.fetchall() == [("新聊天已经完整发布",)]
    assert fake_db.tables == {publication["table_name"]: 1}
