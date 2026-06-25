from django.contrib import admin

from web.models.friend import Friend, Message,SystemPrompt
# Register your models here.
from web.models.user import UserProfile
from web.models.character import Character,Voice
from web.models.memory import EpisodicMemory, SemanticMemory

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    raw_id_fields = ('user',)

@admin.register(Character)
class CharacterAdmin(admin.ModelAdmin):
    raw_id_fields = ('author','voice')


admin.site.register(Voice)

@admin.register(Friend)
class FriendAdmin(admin.ModelAdmin):
    raw_id_fields = ('me','character',)

@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    raw_id_fields = ('friend',)


admin.site.register(SystemPrompt)


@admin.register(SemanticMemory)
class SemanticMemoryAdmin(admin.ModelAdmin):
    list_display = ("fact", "friend", "category", "source", "is_locked", "is_active", "confidence")
    list_filter = ("category", "source", "is_locked", "is_active")
    search_fields = ("fact", "friend__me__user__username", "friend__character__name")
    raw_id_fields = ("friend", "replaced_by")


@admin.register(EpisodicMemory)
class EpisodicMemoryAdmin(admin.ModelAdmin):
    list_display = ("summary", "friend", "importance", "created_at")
    search_fields = ("summary", "keywords")
    raw_id_fields = ("friend",)
