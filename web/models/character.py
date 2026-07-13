import uuid

from django.db import models
from django.utils.timezone import now, localtime

from web.models.user import UserProfile, photo_upload_to

def photo_upload_to(instance, filename):
    ext = filename.split('.')[-1]
    filename = f'{uuid.uuid4().hex[:10]}.{ext}'
    return f'character/photos/{instance.author.user_id}_{filename}'

def background_image_upload_to(instance, filename):
    ext = filename.split('.')[-1]
    filename = f'{uuid.uuid4().hex[:10]}.{ext}'
    return f'character/background_images/{instance.author.user_id}_{filename}'

class Voice(models.Model):
    name = models.CharField(max_length=100)
    voice_id = models.CharField(max_length=100)
    create_time = models.DateTimeField(default=now)

    def __str__(self):
        return f"{self.name} - {self.voice_id} - {localtime(self.create_time).strftime('%Y-%m-%d %H:%M:%S')}"

class Character(models.Model):
    IMPORTED_MEMORY_VISIBILITY_CHOICES = [
        ("private", "仅自己可用"),
        ("public", "所有使用该角色的用户可用"),
    ]

    author = models.ForeignKey(UserProfile, on_delete=models.CASCADE)
    name = models.CharField(max_length=50)
    photo = models.ImageField(upload_to=photo_upload_to)
    voice = models.ForeignKey(Voice,default=None, on_delete=models.CASCADE,blank=True, null=True)
    profile=models.TextField(max_length=100000)
    style_profile = models.TextField(default="", blank=True, max_length=2000)
    imported_memory_visibility = models.CharField(
        max_length=10,
        choices=IMPORTED_MEMORY_VISIBILITY_CHOICES,
        default="private",
        help_text="控制导入聊天原文及其派生记忆是否允许其他用户检索",
    )
    background_image = models.ImageField(upload_to=background_image_upload_to)
    chat_sender_name = models.CharField(max_length=50, default="", blank=True, help_text="聊天记录中此角色对应的发送人名字（如大白鹅）")
    create_time = models.DateTimeField(default=now)
    update_time = models.DateTimeField(default=now)
    def __str__(self):
        return f"{self.author.user.username} - {self.name} - {localtime(self.create_time).strftime('%Y-%m-%d %H:%M:%S')}"
