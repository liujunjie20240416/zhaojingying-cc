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
            ("partial", "部分完成，可断点继续"),
            ("done", "已完成"),
            ("failed", "失败"),
        ],
    )
    error_message = models.TextField(default="")
    total_chunks = models.PositiveIntegerField(default=0)
    completed_chunks = models.PositiveIntegerField(default=0)
    failed_chunks = models.PositiveIntegerField(default=0)
    stage = models.CharField(max_length=30, default="pending")
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "import_analysis"

    def __str__(self):
        return f"{self.character.name} — {self.status} ({self.total_messages} 条消息)"


class PreprocessingCheckpoint(models.Model):
    """导入预处理 Map 阶段的可恢复断点。

    只复用 source_fingerprint 和 chunk_fingerprint 都一致的成功结果；原始聊天
    发生变化时会自然进入一组新断点，不会把旧分析混入新导入。
    """

    character = models.ForeignKey(Character, on_delete=models.CASCADE)
    source_fingerprint = models.CharField(max_length=64)
    chunk_index = models.PositiveIntegerField()
    chunk_fingerprint = models.CharField(max_length=64)
    result_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "preprocessing_checkpoint"
        constraints = [
            models.UniqueConstraint(
                fields=["character", "source_fingerprint", "chunk_index"],
                name="unique_character_source_chunk",
            ),
        ]
        indexes = [
            models.Index(
                fields=["character", "source_fingerprint"],
                name="checkpoint_char_source",
            ),
        ]


class TimeChunk(models.Model):
    """时间标签 — 给消息段打上自然语义标签（如 "2024年夏天 · 暧昧期"）"""

    character = models.ForeignKey(Character, on_delete=models.CASCADE)
    label = models.CharField(max_length=100)             # "2024年夏天 · 暧昧期"
    start_msg_index = models.IntegerField()
    end_msg_index = models.IntegerField()
    summary = models.TextField(default="")                # 日级摘要（条件式 LLM Reduce）
    key_events = models.TextField(default="[]")           # JSON: ["第一次约会", "吵架"]

    class Meta:
        db_table = "time_chunk"
        indexes = [
            models.Index(fields=["character", "start_msg_index"], name="tc_char_sidx"),
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
