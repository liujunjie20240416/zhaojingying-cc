"""
聊天消息存储模型（供 FTS5 全文搜索）

导入聊天记录时同时写入此表，FTS5 虚拟表会实时同步索引。
"""

from django.db import models
from django.utils.timezone import now

from web.models.character import Character


class ChatMessage(models.Model):
    """微信聊天消息原始记录"""

    character = models.ForeignKey(Character, on_delete=models.CASCADE)
    sender = models.CharField(max_length=100)       # 发送人（大白鹅 / 2024.4.16）
    content = models.TextField()                     # 消息内容
    timestamp = models.CharField(max_length=50)      # 原始时间戳 "2022-03-12 13:21:50"
    msg_index = models.IntegerField(default=0)       # 消息序号
    create_time = models.DateTimeField(default=now)

    class Meta:
        db_table = "chat_message"
        indexes = [
            models.Index(fields=["character", "sender"]),
            models.Index(fields=["character", "msg_index"]),
        ]

    def __str__(self):
        return f"[{self.timestamp}] {self.sender}: {self.content[:50]}"
