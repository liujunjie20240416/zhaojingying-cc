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


# ── Friend ──
class GetOrCreateFriendRequest(BaseModel):
    character_id: int


class RemoveFriendRequest(BaseModel):
    friend_id: int


# ── Chat ──
class ChatRequest(BaseModel):
    friend_id: int
    message: str


class MemoryCreateRequest(BaseModel):
    friend_id: int
    fact: str = Field(min_length=1, max_length=500)
    category: str


class MemoryUpdateRequest(BaseModel):
    fact: str = Field(min_length=1, max_length=500)
    category: str
