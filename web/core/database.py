"""
Database setup and session management for the web application.

This module initializes all database engines, enables WAL mode for concurrent
access, and provides FastAPI dependency functions to get database sessions.
"""

import os
from sqlalchemy import inspect
from sqlmodel import create_engine, Session
from web.ocr_models import OCRPlayerMapping, OCREventData, UserAvatarCache

# --- Database Setup ---
DB_DIR = os.path.abspath("db")
os.makedirs(DB_DIR, exist_ok=True)

users_engine = create_engine(f"sqlite:///{os.path.join(DB_DIR, 'users.sqlite')}", connect_args={"check_same_thread": False})
giftcode_engine = create_engine(f"sqlite:///{os.path.join(DB_DIR, 'giftcode.sqlite')}", connect_args={"check_same_thread": False})
changes_engine = create_engine(f"sqlite:///{os.path.join(DB_DIR, 'changes.sqlite')}", connect_args={"check_same_thread": False})
attendance_engine = create_engine(f"sqlite:///{os.path.join(DB_DIR, 'attendance.sqlite')}", connect_args={"check_same_thread": False})
beartime_engine = create_engine(f"sqlite:///{os.path.join(DB_DIR, 'beartime.sqlite')}", connect_args={"check_same_thread": False})
alliance_engine = create_engine(f"sqlite:///{os.path.join(DB_DIR, 'alliance.sqlite')}", connect_args={"check_same_thread": False})
cache_engine = create_engine(f"sqlite:///{os.path.join(DB_DIR, 'web_cache.sqlite')}", connect_args={"check_same_thread": False})

def initialize_ocr_database():
    """
    Creates OCR-related tables in the cache database if they do not exist.

    This function inspects the cache database and creates the necessary tables
    for OCR data storage, ensuring the application can start without errors.
    """
    inspector = inspect(cache_engine)
    existing_tables = inspector.get_table_names()
    if 'ocr_player_mapping' not in existing_tables:
        OCRPlayerMapping.__table__.create(cache_engine)
    if 'ocr_event_data' not in existing_tables:
        OCREventData.__table__.create(cache_engine)
    if 'user_avatar_cache' not in existing_tables:
        UserAvatarCache.__table__.create(cache_engine)

def enable_wal_mode():
    """
    Enables Write-Ahead Logging (WAL) mode for all SQLite databases.

    WAL mode allows for concurrent read and write access, which is essential
    for a web application handling multiple requests.
    """
    engines = [
        users_engine, giftcode_engine, changes_engine,
        attendance_engine, beartime_engine, alliance_engine, cache_engine
    ]
    for engine in engines:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL;")

# --- Dependency Functions ---

def get_users_session():
    """FastAPI dependency to get a session for the users database."""
    with Session(users_engine) as session:
        yield session

def get_giftcode_session():
    """FastAPI dependency to get a session for the giftcode database."""
    with Session(giftcode_engine) as session:
        yield session

def get_changes_session():
    """FastAPI dependency to get a session for the changes database."""
    with Session(changes_engine) as session:
        yield session

def get_attendance_session():
    """FastAPI dependency to get a session for the attendance database."""
    with Session(attendance_engine) as session:
        yield session

def get_beartime_session():
    """FastAPI dependency to get a session for the beartime database."""
    with Session(beartime_engine) as session:
        yield session

def get_alliance_session():
    """FastAPI dependency to get a session for the alliance database."""
    with Session(alliance_engine) as session:
        yield session

def get_cache_session():
    """FastAPI dependency to get a session for the cache database."""
    with Session(cache_engine) as session:
        yield session
