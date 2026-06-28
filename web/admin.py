from django.contrib import admin

from web.models.friend import Friend, Message,SystemPrompt
# Register your models here.
from web.models.user import UserProfile
from web.models.character import Character,Voice
from web.models.memory import EpisodicMemory, SemanticMemory
from web.models.import_analysis import ImportAnalysis, TimeChunk, TopicTag

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
    list_display = ("fact", "friend", "subject", "category", "memory_state", "source", "is_locked", "is_mutable", "is_active", "confidence")
    list_filter = ("subject", "category", "memory_state", "source", "is_locked", "is_mutable", "is_active")
    search_fields = ("fact", "friend__me__user__username", "friend__character__name")
    raw_id_fields = ("friend", "replaced_by")


@admin.register(ImportAnalysis)
class ImportAnalysisAdmin(admin.ModelAdmin):
    list_display = ("character", "status", "total_messages", "created_at")
    list_filter = ("status",)
    raw_id_fields = ("character",)


@admin.register(TimeChunk)
class TimeChunkAdmin(admin.ModelAdmin):
    list_display = ("label", "character", "start_msg_index", "end_msg_index")
    search_fields = ("label", "character__name")


@admin.register(TopicTag)
class TopicTagAdmin(admin.ModelAdmin):
    list_display = ("tag", "character")
    search_fields = ("tag", "character__name")


@admin.register(EpisodicMemory)
class EpisodicMemoryAdmin(admin.ModelAdmin):
    list_display = ("summary", "friend", "importance", "created_at")
    search_fields = ("summary", "keywords")
    raw_id_fields = ("friend",)
