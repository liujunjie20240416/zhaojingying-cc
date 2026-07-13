from django.core.management.base import BaseCommand, CommandError

from ai.preprocessing.pipeline import run_preprocessing
from web.models.character import Character
from web.models.chat_message import ChatMessage


class Command(BaseCommand):
    help = "从成功的 Chunk 断点继续导入聊天预处理"

    def add_arguments(self, parser):
        parser.add_argument("--character-id", type=int, required=True)
        parser.add_argument("--workers", type=int, default=3)
        parser.add_argument(
            "--confirm-private-data",
            action="store_true",
            help="确认会把尚未处理的私人聊天片段发送给当前配置的 GLM",
        )

    def handle(self, *args, **options):
        if not options["confirm_private_data"]:
            raise CommandError(
                "该操作会把尚未处理的私人聊天发送给 GLM；"
                "确认后增加 --confirm-private-data"
            )
        character_id = options["character_id"]
        if not Character.objects.filter(id=character_id).exists():
            raise CommandError("角色不存在")
        if not ChatMessage.objects.filter(character_id=character_id).exists():
            raise CommandError("没有可恢复的聊天原文")
        self.stdout.write(f"开始恢复角色 {character_id} 的预处理...")
        run_preprocessing(
            character_id=character_id,
            max_workers=max(1, min(options["workers"], 5)),
        )
        self.stdout.write(self.style.SUCCESS("预处理命令执行结束，请检查最终状态"))
