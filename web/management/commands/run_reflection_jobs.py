from django.core.management.base import BaseCommand
import time

from ai.memory.reflection_jobs import (
    enqueue_completed_chat_days,
    process_pending_reflection_jobs,
)
from web.models.friend import Friend


class Command(BaseCommand):
    help = "创建并处理持久化的聊天日 Reflection 任务"

    def add_arguments(self, parser):
        parser.add_argument("--friend-id", type=int)
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--watch", action="store_true")
        parser.add_argument("--poll-seconds", type=int, default=30)

    def handle(self, *args, **options):
        while True:
            self._run_once(options)
            if not options["watch"]:
                return
            try:
                time.sleep(max(5, options["poll_seconds"]))
            except KeyboardInterrupt:
                self.stdout.write("Reflection worker 已停止")
                return

    def _run_once(self, options):
        friends = Friend.objects.all()
        if options.get("friend_id"):
            friends = friends.filter(id=options["friend_id"])
        enqueued = 0
        for friend in friends:
            enqueued += len(enqueue_completed_chat_days(friend))
        result = process_pending_reflection_jobs(
            friend_id=options.get("friend_id"), limit=options["limit"]
        )
        self.stdout.write(self.style.SUCCESS(
            f"已发现 {enqueued} 个待处理任务；完成 {result['done']}，失败 {result['failed']}"
        ))
