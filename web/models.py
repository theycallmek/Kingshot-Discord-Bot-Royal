from typing import Optional, List
from sqlmodel import Field, SQLModel, Relationship
from datetime import datetime, time

class GiftCode(SQLModel, table=True):
    __tablename__ = "gift_codes"
    giftcode: str = Field(primary_key=True)
    date: str

    users: List["UserGiftCode"] = Relationship(back_populates="gift")

class User(SQLModel, table=True):
    __tablename__ = "users"
    fid: int = Field(primary_key=True)
    nickname: str
    furnace_lv: int = 0
    kid: int
    stove_lv_content: str
    alliance: str

    gift_codes: List["UserGiftCode"] = Relationship(back_populates="user")

class UserGiftCode(SQLModel, table=True):
    __tablename__ = "user_giftcodes"
    fid: int = Field(foreign_key="users.fid", primary_key=True)
    giftcode: str = Field(foreign_key="gift_codes.giftcode", primary_key=True)
    status: str

    user: Optional["User"] = Relationship(back_populates="gift_codes")
    gift: Optional["GiftCode"] = Relationship(back_populates="users")

class NicknameChange(SQLModel, table=True):
    __tablename__ = "nickname_changes"
    id: Optional[int] = Field(default=None, primary_key=True)
    fid: int
    old_nickname: str
    new_nickname: str
    change_date: str

class FurnaceChange(SQLModel, table=True):
    __tablename__ = "furnace_changes"
    id: Optional[int] = Field(default=None, primary_key=True)
    fid: int
    old_furnace_lv: int
    new_furnace_lv: int
    change_date: str

class AttendanceRecord(SQLModel, table=True):
    __tablename__ = "attendance_records"
    record_id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str
    session_name: str
    event_type: str
    event_date: Optional[datetime] = None
    player_id: str
    player_name: str
    alliance_id: str
    alliance_name: str
    status: str
    points: int = 0
    marked_at: Optional[datetime] = None
    marked_by: Optional[str] = None
    marked_by_username: Optional[str] = None
    created_at: Optional[datetime] = None

class BearNotification(SQLModel, table=True):
    __tablename__ = "bear_notifications"
    id: Optional[int] = Field(default=None, primary_key=True)
    guild_id: int
    channel_id: int
    hour: int
    minute: int
    timezone: str
    description: str
    notification_type: int
    mention_type: str
    repeat_enabled: int
    repeat_minutes: int
    is_enabled: int
    created_at: datetime
    created_by: int
    last_notification: Optional[datetime] = None
    next_notification: Optional[datetime] = None
