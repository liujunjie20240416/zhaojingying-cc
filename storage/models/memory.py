from django.db import models
from django.utils.timezone import now

from storage.models.friend import Friend


class SemanticMemory(models.Model):
    """语义记忆 — 提炼的长期事实和偏好"""
    SUBJECT_CHOICES = [
        ("user", "用户"),
        ("girlfriend", "女友"),
        ("relationship", "两人关系"),
    ]
    CATEGORY_CHOICES = [
        ("identity", "身份"),
        ("preference", "偏好"),
        ("experience", "经历"),
        ("relationship", "互动规律"),
    ]
    SOURCE_CHOICES = [
        ("ai", "AI 自动整理"),
        ("user", "用户手动维护"),
        ("import", "导入聊天记录预处理"),
    ]
    MEMORY_STATE_CHOICES = [
        ("current", "当前有效"),
        ("historical", "历史状态"),
        ("superseded", "已替代"),
    ]

    friend = models.ForeignKey(Friend, on_delete=models.CASCADE)
    subject = models.CharField(max_length=20, choices=SUBJECT_CHOICES, default="user")
    fact = models.CharField(max_length=500)
    # A stable subject for a changing fact, e.g. ``user.preference.spiciness``.
    # Unlike fact text, this lets "喜欢辣 → 暂时不能吃 → 又能吃辣" remain one
    # recoverable timeline instead of three unrelated rows.
    trajectory_key = models.CharField(max_length=120, blank=True, default="")
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default="preference")
    confidence = models.FloatField(default=0.5)
    evidence = models.TextField(default="")
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default="ai")
    memory_state = models.CharField(
        max_length=20, choices=MEMORY_STATE_CHOICES, default="current"
    )
    is_locked = models.BooleanField(default=False)
    is_mutable = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    replaced_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(default=now)
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_to = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "semantic_memory"
        indexes = [
            models.Index(fields=["friend", "subject", "is_active"], name="sem_friend_subject_active"),
            models.Index(fields=["friend", "memory_state", "is_active"], name="sem_friend_state_active"),
            models.Index(fields=["friend", "is_active", "category"], name="sem_friend_active_cat"),
            models.Index(fields=["friend", "-confidence"], name="sem_friend_conf"),
            models.Index(fields=["friend", "trajectory_key", "is_active"], name="sem_friend_trajectory"),
        ]


class MemoryEvidence(models.Model):
    """可追溯证据：把长期事实关联回导入聊天或后续 AI 聊天。"""

    SOURCE_TYPE_CHOICES = [
        ("import_chat", "导入聊天"),
        ("online_chat", "后续 AI 聊天"),
        ("user_assertion", "用户手动维护"),
    ]

    memory = models.ForeignKey(
        SemanticMemory, on_delete=models.CASCADE, related_name="evidences"
    )
    source_type = models.CharField(max_length=20, choices=SOURCE_TYPE_CHOICES)
    # import_chat 保存 ChatMessage.msg_index；online_chat 保存 Message.id。
    message_refs = models.JSONField(default=list, blank=True)
    start_message_ref = models.IntegerField(null=True, blank=True)
    end_message_ref = models.IntegerField(null=True, blank=True)
    chat_day = models.DateField(null=True, blank=True)
    excerpt = models.TextField(default="", blank=True)
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "memory_evidence"
        indexes = [
            models.Index(fields=["memory", "source_type"], name="mem_evidence_source"),
        ]


class ConversationCollapse(models.Model):
    """A read-time projection of one older Online Chat range.

    Raw ``Message`` rows remain authoritative.  A collapse is only a small,
    range-addressable capsule used to find the right historical period before
    expanding the supporting facts or raw messages.
    """

    friend = models.ForeignKey(Friend, on_delete=models.CASCADE, related_name="conversation_collapses")
    start_message_id = models.PositiveBigIntegerField()
    end_message_id = models.PositiveBigIntegerField()
    summary = models.TextField(max_length=2000)
    topics = models.JSONField(default=list, blank=True)
    token_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "conversation_collapse"
        constraints = [
            models.UniqueConstraint(
                fields=["friend", "start_message_id", "end_message_id"],
                name="unique_friend_collapse_range",
            ),
        ]
        indexes = [
            models.Index(fields=["friend", "start_message_id"], name="collapse_friend_start"),
            models.Index(fields=["friend", "end_message_id"], name="collapse_friend_end"),
        ]
