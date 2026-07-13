"""Durable chat-day Reflection queue backed by the primary database."""

import datetime
import logging

from django.db.models import F, Q
from django.utils.timezone import now

from ai.memory.chat_day import detect_day_start_hour_from_datetimes, get_chat_day
from ai.memory.reflection import reflect_memories
from web.models.friend import Friend, Message
from web.models.reflection_job import ReflectionJob

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
STALE_AFTER = datetime.timedelta(minutes=30)


def enqueue_completed_chat_days(friend: Friend) -> list[int]:
    """Persist one unique job for every completed, not-yet-processed chat day."""
    datetimes = list(Message.objects.filter(friend=friend).order_by(
        "create_time"
    ).values_list("create_time", flat=True))
    if not datetimes:
        return []
    day_start_hour = detect_day_start_hour_from_datetimes(datetimes)
    current_chat_day = get_chat_day(now(), day_start_hour)
    completed_days = sorted({
        get_chat_day(value, day_start_hour)
        for value in datetimes
        if get_chat_day(value, day_start_hour) < current_chat_day
    })
    if friend.last_reflected_chat_day:
        completed_days = [
            day for day in completed_days if day > friend.last_reflected_chat_day
        ]

    job_ids = []
    for chat_day in completed_days:
        job, _ = ReflectionJob.objects.get_or_create(
            friend=friend,
            chat_day=chat_day,
            defaults={"status": "pending", "updated_at": now()},
        )
        if job.status != "done":
            job_ids.append(job.id)
    return job_ids


def _claim_next_job(friend_id: int | None = None) -> ReflectionJob | None:
    stale_before = now() - STALE_AFTER
    eligible = (
        Q(status="pending")
        | Q(status="failed", attempts__lt=MAX_ATTEMPTS)
        | Q(status="running", locked_at__lt=stale_before, attempts__lt=MAX_ATTEMPTS)
    )
    queryset = ReflectionJob.objects.filter(eligible)
    if friend_id is not None:
        queryset = queryset.filter(friend_id=friend_id)

    for job_id in queryset.order_by("chat_day", "id").values_list("id", flat=True)[:10]:
        claimed_at = now()
        updated = ReflectionJob.objects.filter(id=job_id).filter(eligible).update(
            status="running",
            attempts=F("attempts") + 1,
            locked_at=claimed_at,
            error_message="",
            updated_at=claimed_at,
        )
        if updated:
            return ReflectionJob.objects.select_related("friend").get(id=job_id)
    return None


def process_pending_reflection_jobs(
    friend_id: int | None = None,
    limit: int = 3,
) -> dict[str, int]:
    """Claim and process durable jobs; safe for concurrent workers."""
    result = {"done": 0, "failed": 0}
    for _ in range(max(0, limit)):
        job = _claim_next_job(friend_id)
        if not job:
            break
        try:
            reflect_memories(job.friend, target_chat_day=job.chat_day)
            ReflectionJob.objects.filter(id=job.id).update(
                status="done", locked_at=None, error_message="", updated_at=now()
            )
            result["done"] += 1
        except Exception as exc:
            logger.exception("Reflection job %s failed", job.id)
            ReflectionJob.objects.filter(id=job.id).update(
                status="failed",
                locked_at=None,
                error_message=str(exc)[:2000],
                updated_at=now(),
            )
            result["failed"] += 1
    return result
