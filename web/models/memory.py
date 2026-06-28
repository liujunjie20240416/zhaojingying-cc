from django.db import models
from django.utils.timezone import now

from web.models.friend import Friend


class EpisodicMemory(models.Model):
    """情景记忆 — 每轮对话抽象为一个事件"""
    friend = models.ForeignKey(Friend, on_delete=models.CASCADE)
    summary = models.CharField(max_length=200)
    keywords = models.CharField(max_length=200, default="")
    importance = models.FloatField(default=0.5)
    raw_messages = models.TextField()
    msg_count = models.IntegerField(default=1)
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "episodic_memory"
        indexes = [
            models.Index(fields=["friend", "-created_at"], name="ep_friend_created"),
        ]


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
        ]
