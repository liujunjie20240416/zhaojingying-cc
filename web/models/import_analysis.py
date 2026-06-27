"""导入聊天记录预处理结果模型 — 用户画像 / 时间线 / 话题标签"""

from django.db import models
from django.utils.timezone import now

from web.models.character import Character


class ImportAnalysis(models.Model):
    """导入分析结果 — 一个 Character 一条记录。

    user_profile: 保留字段（已迁移到 SemanticMemory source="import"）
    relationship_overview: 关系演变宏观描述，注入 system prompt
    """

    character = models.OneToOneField(Character, on_delete=models.CASCADE)
    total_messages = models.IntegerField(default=0)
    user_profile = models.TextField(default="")
    relationship_overview = models.TextField(default="")
    timeline_json = models.TextField(default="[]")
    status = models.CharField(
        max_length=20,
        default="pending",
        choices=[
            ("pending", "待分析"),
            ("analyzing", "分析中"),
            ("done", "已完成"),
            ("failed", "失败"),
        ],
    )
    error_message = models.TextField(default="")
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "import_analysis"

    def __str__(self):
        return f"{self.character.name} — {self.status} ({self.total_messages} 条消息)"


class TimeChunk(models.Model):
    """时间标签 — 给消息段打上自然语义标签（如 "2024年夏天 · 暧昧期"）"""

    character = models.ForeignKey(Character, on_delete=models.CASCADE)
    label = models.CharField(max_length=100)             # "2024年夏天 · 暧昧期"
    start_msg_index = models.IntegerField()
    end_msg_index = models.IntegerField()
    summary = models.CharField(max_length=200)            # 一句话总结
    key_events = models.TextField(default="[]")           # JSON: ["第一次约会", "吵架"]

    class Meta:
        db_table = "time_chunk"
        indexes = [
            models.Index(fields=["character", "start_msg_index"]),
        ]

    def __str__(self):
        return f"{self.label} ({self.start_msg_index}-{self.end_msg_index})"


class TopicTag(models.Model):
    """话题标签 — 每个话题关联的消息 index 列表"""

    character = models.ForeignKey(Character, on_delete=models.CASCADE)
    tag = models.CharField(max_length=100)                # "美食/火锅"
    msg_indices = models.TextField(default="[]")          # JSON: [12, 45, 78, 102]

    class Meta:
        db_table = "topic_tag"

    def __str__(self):
        return f"{self.tag} ({self.character.name})"
