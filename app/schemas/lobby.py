from __future__ import annotations

from pydantic import BaseModel, Field


class CreateRoomRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=30)
    is_private: bool = False
    password: str | None = Field(default=None, pattern=r"^\d{4}$")
    max_players: int = Field(default=4, ge=2, le=4)


class JoinRoomRequest(BaseModel):
    password: str | None = Field(default=None, pattern=r"^\d{4}$")


class RoomCardResponse(BaseModel):
    id: str
    title: str
    status: str
    current_players: int
    max_players: int
    is_private: bool
    host_id: str
    host_nickname: str


class RoomListResponse(BaseModel):
    rooms: list[RoomCardResponse]
    total: int = 0


class RoomMemberResponse(BaseModel):
    id: str
    nickname: str
    is_ready: bool
    is_host: bool


class RoomChatMessageResponse(BaseModel):
    id: str
    sender_id: str
    sender_nickname: str
    message: str
    sent_at: str
    type: str


class RoomSnapshotResponse(BaseModel):
    room_id: str
    title: str
    status: str
    max_players: int
    is_private: bool
    players: list[RoomMemberResponse]
    chat_messages: list[RoomChatMessageResponse]


class LeaveRoomResponse(BaseModel):
    success: bool
    new_host_id: str | None = None


class ToggleReadyResponse(BaseModel):
    is_ready: bool


class StartGameResponse(BaseModel):
    success: bool
    game_id: str
