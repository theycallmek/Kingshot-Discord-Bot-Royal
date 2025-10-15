from typing import Optional
from sqlmodel import Field, SQLModel

class User(SQLModel, table=True):
    __tablename__ = "users"
    fid: Optional[int] = Field(default=None, primary_key=True)
    nickname: str
    furnace_lv: int = 0
    kid: int
    stove_lv_content: str
    alliance: str
