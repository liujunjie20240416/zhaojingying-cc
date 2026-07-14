

from django.db import models
from django.utils.timezone import now, localtime

from web.models.character import Character
from web.models.user import UserProfile


class Friend(models.Model):
    me = models.ForeignKey(UserProfile, on_delete=models.CASCADE)
    character = models.ForeignKey(Character, on_delete=models.CASCADE)
    memory = models.TextField(default="",max_length=5000,blank=True,null=True)
    conversation_summary = models.TextField(default="", blank=True, max_length=2000)
    summary_through_message_id = models.PositiveBigIntegerField(null=True, blank=True)
    summary_updated_at = models.DateTimeField(null=True, blank=True)
    online_history_generation = models.PositiveBigIntegerField(default=0)
    last_reflection_time = models.DateTimeField(default=now)
    last_reflected_chat_day = models.DateField(null=True, blank=True)
    create_time = models.DateTimeField(default=now)
    update_time = models.DateTimeField(default=now)

    def __str__(self):
        return f"{self.character.name}-{self.me.user.username}-{localtime(self.create_time).strftime('%Y/%m/%d %H:%M:%S') }"


class Message(models.Model):
    friend = models.ForeignKey(Friend, on_delete=models.CASCADE)
    user_message = models.TextField(max_length=500)
    input = models.TextField(max_length=10000)
    output = models.TextField()
    output_bubbles = models.JSONField(default=list, blank=True)
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    total_tokens = models.IntegerField(default=0)
    create_time = models.DateTimeField(default=now)

    def __str__(self):
        return f"{self.friend.character.name} - {self.friend.me.user.username} - {self.user_message[:50] }- {localtime(self.create_time).strftime('%Y/%m/%d %H:%M:%S')} "


class MessageAttachment(models.Model):
    friend = models.ForeignKey(Friend, on_delete=models.CASCADE, related_name="attachments")
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="attachments", null=True, blank=True
    )
    file = models.ImageField(upload_to="chat_images/%Y/%m/%d/")
    mime_type = models.CharField(max_length=64)
    file_size = models.PositiveIntegerField()
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    sha256 = models.CharField(max_length=64, db_index=True)
    caption = models.TextField(blank=True, default="", max_length=1000)
    create_time = models.DateTimeField(default=now)

    def __str__(self):
        return f"attachment-{self.id}-friend-{self.friend_id}"

class SystemPrompt(models.Model):
    title = models.CharField(max_length=100)
    order_number = models.IntegerField(default=0)
    prompt = models.TextField(max_length=10000)
    create_time = models.DateTimeField(default=now)
    update_time = models.DateTimeField(default=now)

    def __str__(self):
        return f"{self.title} - {self.order_number} - {self.prompt[:50]} - {localtime(self.create_time).strftime('%Y/%m/%d %H:%M:%S')} "
