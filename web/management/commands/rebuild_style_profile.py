from django.core.management.base import BaseCommand, CommandError

from ai.preprocessing.style_analyzer import analyze_style_profile
from web.models.character import Character


class Command(BaseCommand):
    help = "仅依据导入聊天显式重建稳定的角色说话风格，不重跑记忆 Map/Reduce"

    def add_arguments(self, parser):
        parser.add_argument("--character-id", type=int, required=True)
        parser.add_argument(
            "--confirm-private-data",
            action="store_true",
            help="确认会把抽样聊天发送到当前配置的 LLM",
        )

    def handle(self, *args, **options):
        if not options["confirm_private_data"]:
            raise CommandError(
                "该操作会把抽样聊天发送到当前配置的 LLM；"
                "确认后增加 --confirm-private-data"
            )
        character = Character.objects.filter(id=options["character_id"]).first()
        if not character:
            raise CommandError("角色不存在")
        target_name = character.chat_sender_name or character.name
        style_profile = analyze_style_profile(character.id, target_name, [])
        if not style_profile.strip():
            raise CommandError("没有可用聊天记录，未生成说话风格")
        character.style_profile = style_profile
        character.save(update_fields=["style_profile"])
        self.stdout.write(self.style.SUCCESS(
            f"角色 {character.id} 的说话风格已更新（{len(style_profile)} 字）"
        ))
