from django.db import models
from django.utils.timezone import now

from web.models.friend import Friend


class ReflectionJob(models.Model):
    """A durable, idempotent chat-day reflection task."""

    STATUS_CHOICES = [
        ("pending", "等待处理"),
        ("running", "处理中"),
        ("done", "已完成"),
        ("failed", "处理失败"),
    ]

    friend = models.ForeignKey(Friend, on_delete=models.CASCADE)
    chat_day = models.DateField()
    history_generation = models.PositiveBigIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    attempts = models.PositiveIntegerField(default=0)
    locked_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(default="", blank=True)
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "reflection_job"
        constraints = [
            models.UniqueConstraint(
                fields=["friend", "chat_day"], name="unique_friend_reflection_day"
            ),
        ]
        indexes = [
            models.Index(fields=["status", "chat_day"], name="reflection_status_day"),
        ]
