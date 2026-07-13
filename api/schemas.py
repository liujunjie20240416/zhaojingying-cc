from pydantic import BaseModel, Field


# ── Auth ──
class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str


# ── Character ──
class RemoveCharacterRequest(BaseModel):
    character_id: int


class ResumeImportRequest(BaseModel):
    character_id: int


class UpdateImportedMemoryVisibilityRequest(BaseModel):
    character_id: int
    visibility: str


# ── Friend ──
class GetOrCreateFriendRequest(BaseModel):
    character_id: int


class RemoveFriendRequest(BaseModel):
    friend_id: int


# ── Chat ──
class ChatRequest(BaseModel):
    friend_id: int
    message: str = ""
    attachment_ids: list[int] = Field(default_factory=list, max_length=4)
    emotion_context: list[dict[str, str]] = Field(default_factory=list)


class MemoryCreateRequest(BaseModel):
    friend_id: int
    fact: str = Field(min_length=1, max_length=500)
    category: str
    subject: str = "user"


class MemoryUpdateRequest(BaseModel):
    fact: str = Field(min_length=1, max_length=500)
    category: str
    subject: str = "user"
