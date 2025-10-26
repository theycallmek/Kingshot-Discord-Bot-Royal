"""
Database models for OCR data caching
Streamlined to avoid duplicates with other databases
"""

from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime


class OCRPlayerMapping(SQLModel, table=True):
    """
    Maps OCR-detected player names to FIDs
    This is the core table for matching players from screenshots
    """
    __tablename__ = "ocr_player_mapping"

    mapping_id: Optional[int] = Field(default=None, primary_key=True)
    player_name: str = Field(index=True)  # OCR name (e.g., "[DOA]PlayerName")
    player_fid: str  # FID from users.sqlite, or "0000000000" for unmatched
    confidence: float  # Match confidence (0.0-1.0)
    first_seen: datetime  # When first detected
    last_seen: datetime  # Most recent detection
    times_seen: int = 1  # Number of times this name was detected
    created_at: datetime
    updated_at: datetime


class OCREventData(SQLModel, table=True):
    """
    Stores event-specific data extracted from OCR (scores, ranks, times, etc.)
    Links player names to their performance in specific events
    """
    __tablename__ = "ocr_event_data"

    event_data_id: Optional[int] = Field(default=None, primary_key=True)
    event_name: str = Field(index=True)  # Event name
    event_type: str  # "Damage Rewards", "Bear Trap", "Showdown", etc.
    event_date: datetime  # When the event occurred

    # Player info
    player_name: str = Field(index=True)  # OCR name
    player_fid: Optional[str] = None  # Linked FID (can be null for unmatched)

    # Event performance data
    ranking: Optional[int] = None  # Player's rank in event
    rank_inferred: bool = False  # Whether rank was inferred vs OCR detected
    score: Optional[int] = None  # Generic score field
    damage_points: Optional[int] = None  # For damage-based events
    time_value: Optional[str] = None  # For time-based events (stored as string)

    # OCR metadata
    ocr_confidence: float  # OCR detection confidence
    image_source: str  # Source screenshot filename
    processing_session: str  # Session ID for batch processing tracking

    # Verification tracking (consensus from multiple uploads)
    verification_count: int = 1  # Number of independent sessions that detected same score
    verified_sessions: Optional[str] = None  # Comma-separated list of session IDs that verified this data
    data_confidence: float = 1.0  # Confidence multiplier based on verification (1.0 = single source, increases with verifications)

    # Timestamps
    extracted_at: datetime
    created_at: datetime


class UserAvatarCache(SQLModel, table=True):
    """
    Caches player avatar URLs fetched from the game API
    Prevents excessive API calls due to rate limiting (2 seconds between requests)
    """
    __tablename__ = "user_avatar_cache"

    fid: int = Field(primary_key=True)  # Player FID
    avatar_url: Optional[str] = None  # Avatar image URL from API
    last_updated: datetime  # When avatar was last fetched
    created_at: datetime
