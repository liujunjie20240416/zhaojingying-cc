"""从 chat_message 原文重建指定角色的 FTS5 关键词索引（含 jieba 分词 tokens 列）。

用途：FTS5 表结构从 unicode61 单列升级为带 tokens 分词列后，为存量导入数据重建。
chat_message 是权威源，重建过程无 API 调用，本地 SQLite 操作。
"""
from django.core.management.base import BaseCommand, CommandError

from api.import_data import _sync_fts5_table
from ai.import_storage import imported_fts_table_name
from storage.models.chat_message import ChatMessage
from storage.models.character import Character


class Command(BaseCommand):
    help = "从 chat_message 原文重建角色 FTS5 关键词索引（jieba 分词 tokens 列）"

    def add_arguments(self, parser):
        parser.add_argument("--character-id", type=int, required=True)

    def handle(self, *args, **options):
        character_id = options["character_id"]
        count = ChatMessage.objects.filter(character_id=character_id).count()
        if count == 0:
            raise CommandError(f"character_id={character_id} 没有 chat_message 数据")
        version = Character.objects.filter(id=character_id).values_list(
            "import_data_version", flat=True
        ).first() or ""
        table_name = _sync_fts5_table(
            character_id,
            table_name=imported_fts_table_name(character_id, version),
        )
        self.stdout.write(
            self.style.SUCCESS(f"已重建 {table_name}（{count} 条消息，jieba 分词已写入 tokens 列）")
        )
