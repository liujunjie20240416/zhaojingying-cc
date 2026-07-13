from django.core.management.base import BaseCommand, CommandError

from ai.memory.history_search import rebuild_online_history_index
from web.models.friend import Friend


class Command(BaseCommand):
    help = "从保留的 Online Chat 原文重建 Friend 向量索引"

    def add_arguments(self, parser):
        parser.add_argument("--friend-id", type=int, required=True)
        parser.add_argument(
            "--confirm-private-data",
            action="store_true",
            help="确认会把在线聊天发送到当前配置的 Embedding 模型",
        )

    def handle(self, *args, **options):
        if not options["confirm_private_data"]:
            raise CommandError(
                "该操作会把在线聊天发送到当前配置的 Embedding 模型；"
                "确认后增加 --confirm-private-data"
            )
        if not Friend.objects.filter(id=options["friend_id"]).exists():
            raise CommandError("Friend 不存在")
        if not rebuild_online_history_index(options["friend_id"]):
            raise CommandError("Online Chat 向量索引重建失败")
        self.stdout.write(self.style.SUCCESS("Online Chat 向量索引已重建"))
