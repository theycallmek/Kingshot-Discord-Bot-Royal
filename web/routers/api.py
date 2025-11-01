"""
API routes for the web application.

This module defines endpoints for processing data, fetching details,
and performing actions like creating or updating events. These routes
typically return JSON responses.
"""

from fastapi import APIRouter, Depends, Body, UploadFile, File, Form, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session, selectinload
from sqlmodel import select
from typing import List, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import tempfile
from pathlib import Path
from pydantic import BaseModel
import pytz
import json
import ssl
import aiohttp
import asyncio
import hashlib
import os
import traceback
import re


def parse_player_name(player_name: str) -> Tuple[str, str]:
    """
    Parses a player name to extract the alliance tag and username.

    Expects format: [ALLIANCE]Username or similar bracket notation.

    Args:
        player_name: The full player name with alliance tag.

    Returns:
        A tuple of (alliance_tag, username). Returns ('N/A', player_name) if format is invalid.
    """
    parts = player_name.split("]", 1)
    if len(parts) == 2:
        return parts[0][1:], parts[1].strip()
    return "N/A", player_name.strip()


def reconcile_ocr_player_names(
    cache_session: Session,
    changes_session: Session,
    users_session: Session,
) -> Tuple[int, List[dict]]:
    """
    Reconciles OCR player names with current usernames based on nickname changes.

    Returns:
        A tuple of (updated_count, updates_log)
    """
    # Get all users to build current name mapping
    all_users = users_session.exec(select(User)).all()
    user_map = {user.fid: user.nickname for user in all_users}

    # Get all nickname changes
    nickname_changes = changes_session.exec(select(NicknameChange)).all()

    # Build a mapping of old names -> list of (user_fid, current_name)
    old_name_to_user = {}
    for change in nickname_changes:
        # Extract the username without alliance tag for matching
        old_username = re.sub(r"\[.*?\]", "", change.old_nickname).strip().lower()
        current_name = user_map.get(change.fid, "")

        if old_username and current_name:
            if old_username not in old_name_to_user:
                old_name_to_user[old_username] = []
            old_name_to_user[old_username].append((change.fid, current_name))

    # Get all OCR records
    all_ocr_records = cache_session.exec(select(OCREventData)).all()

    updated_count = 0
    updates_log = []

    for record in all_ocr_records:
        # Extract username without alliance tag
        ocr_username = re.sub(r"\[.*?\]", "", record.player_name).strip().lower()

        # Check if this is an old name
        if ocr_username in old_name_to_user:
            fid, current_name = old_name_to_user[ocr_username][0]

            # current_name from the User table already includes the alliance tag
            # (e.g., "[DOA]Hop"), so we use it directly
            new_name = current_name

            # Only update if the current name is different
            if record.player_name != new_name:
                old_name = record.player_name
                record.player_name = new_name

                # If we're matching to a user, update the FID
                if record.player_fid is None or record.player_fid == "0000000000":
                    record.player_fid = str(fid)

                cache_session.add(record)
                updated_count += 1
                updates_log.append({
                    "old_name": old_name,
                    "new_name": new_name,
                    "fid": str(fid)
                })

    # Commit all changes
    cache_session.commit()

    return updated_count, updates_log


from web.core.database import (
    get_attendance_session,
    get_cache_session,
    get_users_session,
    get_beartime_session,
    get_changes_session,
    users_engine,
    cache_engine,
)
from web.core.config import API_SECRET
from web.models import BearNotification, BearNotificationEmbed, NotificationDays, User, NicknameChange
from web.ocr_models import OCREventData, UserAvatarCache
from web.services.ocr import (
    get_ocr_reader,
    preprocess_image_for_ocr,
    extract_player_scores_from_ocr,
    match_and_store_scores,
    mark_attendance_from_scores,
)
from .auth import is_authenticated

# Constants for event handling
REPEAT_TYPE_FIXED = "fixed"
DEFAULT_EVENT_TYPE = "Bear Trap"
OCR_IMPORT_EVENT_TYPE = "OCR Import"
OCR_SYSTEM_USER = "OCR_System"
OCR_SYSTEM_USERNAME = "Automated OCR"
CUSTOM_TIMES_PREFIX = "CUSTOM_TIMES:"
EMBED_MESSAGE_MARKER = "EMBED_MESSAGE:true"
PLAIN_MESSAGE_PREFIX = "PLAIN_MESSAGE:"

router = APIRouter()


class EventUpdate(BaseModel):
    id: int
    description: Optional[str] = None
    channel_id: str
    hour: int
    minute: int
    repeat_minutes: str
    mention_type: Optional[str] = None
    notification_type: int
    is_enabled: bool = True
    next_notification: datetime
    message_type: str
    weekdays: Optional[List[int]] = None
    custom_times: Optional[str] = None
    embed_title: Optional[str] = None
    embed_description: Optional[str] = None
    embed_color: Optional[str] = None
    embed_footer: Optional[str] = None
    embed_author: Optional[str] = None
    embed_image_url: Optional[str] = None
    embed_thumbnail_url: Optional[str] = None
    embed_mention_message: Optional[str] = None


class EventCreate(BaseModel):
    description: Optional[str] = None
    channel_id: str
    hour: int
    minute: int
    timezone: str
    repeat_minutes: str
    mention_type: str
    notification_type: int
    is_enabled: bool = True
    next_notification: datetime
    message_type: str
    weekdays: Optional[List[int]] = None
    custom_times: Optional[str] = None
    embed_title: Optional[str] = None
    embed_description: Optional[str] = None
    embed_color: Optional[str] = None
    embed_footer: Optional[str] = None
    embed_author: Optional[str] = None
    embed_image_url: Optional[str] = None
    embed_thumbnail_url: Optional[str] = None
    embed_mention_message: Optional[str] = None


class DeleteGhostPlayerRequest(BaseModel):
    player_name: str


@router.post("/api/process-attendance")
async def process_attendance(
    files: List[UploadFile] = File(...),
    event_name: str = Form(...),
    event_type: str = Form(DEFAULT_EVENT_TYPE),
    event_date: Optional[str] = Form(None),
    authenticated: bool = Depends(is_authenticated),
    users_session: Session = Depends(get_users_session),
    cache_session: Session = Depends(get_cache_session),
    attendance_session: Session = Depends(get_attendance_session),
    changes_session: Session = Depends(get_changes_session),
):
    """Processes uploaded screenshots for attendance tracking."""
    if not authenticated:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    try:
        # Create temporary directory for uploaded files
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Save uploaded files
            saved_files = []
            for file in files:
                file_path = temp_path / file.filename
                with open(file_path, "wb") as f:
                    content = await file.read()
                    f.write(content)
                saved_files.append(str(file_path))

            # Initialize OCR reader
            reader = get_ocr_reader()

            # Create OCR session ID - use provided event_date or current time
            event_datetime = datetime.now()
            if event_date:
                try:
                    # Parse ISO format date string (e.g., "2025-10-31T14:30:00Z")
                    # Convert to UTC and make naive to ensure consistent timezone handling in SQLite
                    parsed_dt = datetime.fromisoformat(event_date.replace("Z", "+00:00"))
                    event_datetime = parsed_dt.astimezone(pytz.utc).replace(tzinfo=None)
                except (ValueError, AttributeError):
                    event_datetime = datetime.now()

            session_id = (
                f"{event_name.replace(' ', '_')}_{event_datetime.strftime('%Y%m%d_%H%M%S')}"
            )

            # Process each image
            all_player_data = []
            for file_path in saved_files:
                # Preprocess image for improved OCR accuracy
                preprocessed_path = preprocess_image_for_ocr(file_path)

                # Extract data from image using PaddleOCR
                results = reader.predict(str(preprocessed_path))

                # Extract player scores from PaddleOCR results
                player_data = extract_player_scores_from_ocr(
                    results, Path(file_path).name
                )
                all_player_data.extend(player_data)

            # Remove duplicates (keep highest confidence)
            unique_players = {}
            for data in all_player_data:
                name = data["player_name"]
                if (
                    name not in unique_players
                    or data["confidence"] > unique_players[name]["confidence"]
                ):
                    unique_players[name] = data

            player_data_list = list(unique_players.values())

            # Match players and store scores
            matched_count, matched_players, unmatched = match_and_store_scores(
                player_data_list,
                session_id,
                event_name,
                event_type,
                event_datetime,
                users_session,
                cache_session,
            )

            # Commit cache session to save OCREventData and OCRPlayerMapping records
            cache_session.commit()

            # Reconcile player names with current usernames based on nickname changes
            try:
                reconciled_count, _ = reconcile_ocr_player_names(
                    cache_session, changes_session, users_session
                )
            except Exception as e:
                # Log reconciliation errors but don't fail the upload
                print(f"[WARNING] Error during name reconciliation: {e}")
                reconciled_count = 0

            # Mark attendance for both matched and ghost players
            attendance_marked = mark_attendance_from_scores(
                matched_players, unmatched, event_name, session_id, attendance_session
            )

            # Prepare response
            return JSONResponse(
                {
                    "success": True,
                    "session_id": session_id,
                    "summary": {
                        "images_processed": len(files),
                        "players_detected": len(player_data_list),
                        "players_matched": matched_count,
                        "attendance_marked": attendance_marked,
                    },
                    "matched_players": [
                        {
                            "player_name": p["player_name"],
                            "player_fid": p["player_fid"],
                            "ranking": p.get("ranking"),
                            "damage_points": p.get("damage_points"),
                            "confidence": p["confidence"],
                        }
                        for p in matched_players
                    ],
                    "unmatched_players": [
                        {
                            "player_name": p["player_name"],
                            "player_fid": p["player_fid"],  # Will be "0000000000"
                            "ranking": p.get("ranking"),
                            "damage_points": p.get("damage_points"),
                            "confidence": p["confidence"],
                            "image_source": p["image_source"],
                        }
                        for p in unmatched
                    ],
                }
            )

    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"[ERROR] Exception in process_attendance:")
        print(error_trace)
        return JSONResponse(
            {"error": str(e), "traceback": error_trace}, status_code=500
        )


@router.get("/api/dashboard-data")
async def get_dashboard_data(
    authenticated: bool = Depends(is_authenticated),
    cache_session: Session = Depends(get_cache_session),
    attendance_session: Session = Depends(get_attendance_session),
):
    """Gets dashboard statistics for the attendance page."""
    if not authenticated:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    try:
        # Get recent OCR processing sessions (last 10)
        recent_events_query = (
            select(OCREventData).order_by(OCREventData.extracted_at.desc()).limit(100)
        )
        recent_events_data = cache_session.exec(recent_events_query).all()

        # Group by processing_session
        sessions_dict = {}
        for event in recent_events_data:
            session_id = event.processing_session
            if session_id not in sessions_dict:
                # Trim "Notification" and "Event!" from event name
                event_name = event.event_name
                if event_name.endswith(" Notification"):
                    event_name = event_name[:-len(" Notification")]
                if event_name.endswith(" Event!"):
                    event_name = event_name[:-len(" Event!")]

                # Ensure event_date has UTC timezone for proper JavaScript interpretation
                event_date_utc = event.event_date.replace(tzinfo=pytz.utc) if event.event_date.tzinfo is None else event.event_date
                sessions_dict[session_id] = {
                    "session_id": session_id,
                    "event_name": event_name,
                    "event_type": event.event_type,
                    "event_date": event_date_utc.isoformat(),
                    "player_count": 0,
                    "verified_count": 0,
                    "total_verifications": 0,
                    "extracted_at": event.extracted_at.isoformat(),
                }
            sessions_dict[session_id]["player_count"] += 1
            if event.verification_count > 1:
                sessions_dict[session_id]["verified_count"] += 1
            sessions_dict[session_id]["total_verifications"] += event.verification_count

        recent_sessions = sorted(
            sessions_dict.values(), key=lambda x: x["event_date"], reverse=True
        )[:10]

        # Get top players by damage (across all events in last 30 days)
        thirty_days_ago = datetime.now() - timedelta(days=30)
        top_players_query = (
            select(OCREventData)
            .where(OCREventData.event_date >= thirty_days_ago)
            .where(OCREventData.damage_points.isnot(None))
            .order_by(OCREventData.damage_points.desc())
            .limit(50)
        )
        top_players_data = cache_session.exec(top_players_query).all()

        # Group by event to calculate ranks
        event_players = defaultdict(list)
        for record in top_players_data:
            event_players[record.processing_session].append(record)

        # Calculate ranks and stats
        player_stats = {}
        for records in event_players.values():
            sorted_by_damage = sorted(records, key=lambda p: -(p.damage_points or 0))
            for rank_idx, player_record in enumerate(sorted_by_damage, 1):
                name = player_record.player_name
                alliance_tag, username = parse_player_name(name)
                if name not in player_stats:
                    player_stats[name] = {
                        "player_name": username,
                        "alliance_tag": alliance_tag,
                        "player_fid": player_record.player_fid or "0000000000",
                        "total_damage": 0,
                        "event_count": 0,
                        "avg_verification": 0,
                        "best_rank": None,
                        "total_verifications": 0,
                    }
                if (
                    player_stats[name]["best_rank"] is None
                    or rank_idx < player_stats[name]["best_rank"]
                ):
                    player_stats[name]["best_rank"] = rank_idx

        # Sum totals
        player_events = defaultdict(set)
        for record in top_players_data:
            name = record.player_name
            player_stats[name]["total_damage"] += record.damage_points or 0
            player_stats[name]["total_verifications"] += record.verification_count
            player_events[name].add(record.processing_session)

        for name in player_stats:
            player_stats[name]["event_count"] = len(player_events[name])

        # Calculate averages and sort
        for name in player_stats:
            if player_stats[name]["event_count"] > 0:
                player_stats[name]["avg_verification"] = (
                    player_stats[name]["total_verifications"]
                    / player_stats[name]["event_count"]
                )

        top_players = sorted(
            player_stats.values(), key=lambda x: x["total_damage"], reverse=True
        )[:10]

        # Query UserAvatarCache to get avatar URLs for top players
        avatar_cache_data = cache_session.exec(select(UserAvatarCache)).all()
        avatar_map = {str(cache.fid): cache.avatar_url for cache in avatar_cache_data}

        # Add avatar URLs to each top player
        for player in top_players:
            player_fid = str(player.get("player_fid", ""))
            if player_fid != "0000000000":
                # Matched player - get avatar from cache
                player["avatar_url"] = avatar_map.get(
                    player_fid, "/static/images/user-icon.svg"
                )
            else:
                # Ghost player - use generic avatar
                player["avatar_url"] = "/static/images/user-icon.svg"

        # Get verification statistics
        all_event_data = cache_session.exec(select(OCREventData)).all()
        verification_stats = {
            "high_confidence": 0,  # 3+ verifications
            "medium_confidence": 0,  # 2 verifications
            "low_confidence": 0,  # 1 verification
            "total_records": len(all_event_data),
            "avg_confidence": 0,
        }

        total_confidence = 0
        for record in all_event_data:
            if record.verification_count >= 3:
                verification_stats["high_confidence"] += 1
            elif record.verification_count == 2:
                verification_stats["medium_confidence"] += 1
            else:
                verification_stats["low_confidence"] += 1
            total_confidence += record.data_confidence

        if len(all_event_data) > 0:
            verification_stats["avg_confidence"] = total_confidence / len(
                all_event_data
            )

        return JSONResponse(
            {
                "success": True,
                "recent_events": recent_sessions,
                "top_players": top_players,
                "verification_stats": verification_stats,
            }
        )

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/event-details/{session_id}")
async def get_event_details(
    session_id: str,
    authenticated: bool = Depends(is_authenticated),
    cache_session: Session = Depends(get_cache_session),
    users_session: Session = Depends(get_users_session),
):
    """Gets detailed player data for a specific event processing session."""
    if not authenticated:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    try:
        # Get all event data for the given session
        event_data_query = select(OCREventData).where(
            OCREventData.processing_session == session_id
        )
        event_players = cache_session.exec(event_data_query).all()

        if not event_players:
            return JSONResponse({"error": "Event session not found"}, status_code=404)

        # Get all users for nickname mapping
        users = users_session.exec(select(User)).all()
        user_map = {user.fid: user.nickname for user in users}

        # Prepare player details
        players_details = []
        for player in event_players:
            is_matched = player.player_fid and player.player_fid != "0000000000"
            nickname = "N/A"
            if is_matched:
                # FID is stored as an integer in User model, but string in OCREventData
                try:
                    nickname = user_map.get(int(player.player_fid), "Unknown")
                except (ValueError, TypeError):
                    nickname = "Invalid FID"

            alliance_tag, username = parse_player_name(player.player_name)
            players_details.append(
                {
                    "player_name": username,
                    "alliance_tag": alliance_tag,
                    "player_fid": player.player_fid,
                    "is_matched": is_matched,
                    "nickname": nickname,
                    "ranking": player.ranking,
                    "damage_points": player.damage_points,
                    "ocr_confidence": round(player.ocr_confidence * 100, 1),
                    "verification_count": player.verification_count,
                    "image_source": player.image_source,
                }
            )

        # Sort players by damage points (highest first) for rank calculation
        players_details.sort(key=lambda p: -(p.get("damage_points") or 0))

        # Assign calculated ranks based on damage (highest damage = rank 1)
        for idx, player in enumerate(players_details, 1):
            player["calculated_rank"] = idx

        # Query UserAvatarCache to get avatar URLs for matched players
        avatar_cache_data = cache_session.exec(select(UserAvatarCache)).all()
        avatar_map = {str(cache.fid): cache.avatar_url for cache in avatar_cache_data}

        # Add avatar URLs to each player
        for player in players_details:
            if player.get("is_matched"):
                # Look up avatar from cache using player FID
                player_fid = str(player.get("player_fid", ""))
                player["avatar_url"] = avatar_map.get(
                    player_fid, "/static/images/user-icon.svg"
                )
            else:
                # Use generic avatar for ghost players
                player["avatar_url"] = "/static/images/user-icon.svg"

        # Get event metadata from the first player record
        first_player = event_players[0]
        event_info = {
            "session_id": first_player.processing_session,
            "event_name": first_player.event_name,
            "event_date": first_player.event_date.isoformat(),
            "player_count": len(players_details),
        }

        return JSONResponse(
            {"success": True, "event_info": event_info, "players": players_details}
        )

    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"[ERROR] Exception in get_event_details for session {session_id}:")
        print(error_trace)
        return JSONResponse({"error": str(e), "trace": error_trace}, status_code=500)


@router.get("/api/past-events")
async def get_past_events(
    authenticated: bool = Depends(is_authenticated),
    beartime_session: Session = Depends(get_beartime_session),
):
    """Gets a list of the 10 most recently passed events for the attendance dropdown.

    Returns past event instances including those from repeating events,
    similar to how the calendar generates event instances.
    """
    if not authenticated:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    try:
        # Fetch all events from the database
        all_events = beartime_session.exec(
            select(BearNotification)
            .options(selectinload(BearNotification.embeds), selectinload(BearNotification.notification_days))
            .limit(500)
        ).all()

        now = datetime.now()
        if now.tzinfo is None:
            now = pytz.utc.localize(now)

        past_instances = []

        for event in all_events:
            if not event.embeds or not event.embeds[0].title:
                continue

            title = event.embeds[0].title

            # Trim "Notification" and "Event!" from title
            trimmed_title = title
            if trimmed_title.endswith(" Notification"):
                trimmed_title = trimmed_title[:-len(" Notification")]
            if trimmed_title.endswith(" Event!"):
                trimmed_title = trimmed_title[:-len(" Event!")]

            # Exclude "Arena" events
            if "arena" in trimmed_title.lower():
                continue

            # Make sure next_notification has timezone info
            occurrence = event.next_notification
            if occurrence.tzinfo is None:
                occurrence = pytz.utc.localize(occurrence)

            # Non-repeating events: just check if in the past
            if not event.repeat_enabled:
                if occurrence < now:
                    past_instances.append({
                        "name": trimmed_title,
                        "date": occurrence.isoformat(),
                    })
            else:
                # Repeating events: calculate all past instances
                if str(event.repeat_minutes).isdigit() and int(event.repeat_minutes) > 0:
                    # Fixed interval repeating event
                    repeat_minutes = int(event.repeat_minutes)

                    # Calculate backward from next_notification to find past instances
                    current_occurrence = occurrence

                    # Generate all past instances (go back up to 6 months)
                    six_months_ago = now - timedelta(days=180)
                    while current_occurrence > six_months_ago:
                        if current_occurrence < now:
                            past_instances.append({
                                "name": trimmed_title,
                                "date": current_occurrence.isoformat(),
                            })
                        current_occurrence -= timedelta(minutes=repeat_minutes)

                elif event.repeat_minutes == "fixed" and event.notification_days:
                    # Fixed weekday repeating event
                    try:
                        weekdays = set(map(int, event.notification_days.weekday.split("|")))

                        # Go back 6 months and find all matching weekdays
                        six_months_ago = now - timedelta(days=180)
                        check_date = now.date()

                        while check_date >= six_months_ago.date():
                            if check_date.weekday() in weekdays:
                                # Create datetime with the event's time
                                event_time = occurrence.time()
                                past_occurrence = datetime.combine(
                                    check_date,
                                    event_time,
                                    tzinfo=occurrence.tzinfo,
                                )
                                if past_occurrence < now:
                                    past_instances.append({
                                        "name": trimmed_title,
                                        "date": past_occurrence.isoformat(),
                                    })
                            check_date -= timedelta(days=1)
                    except (ValueError, TypeError, AttributeError):
                        pass

        # Remove duplicates while preserving order
        seen = set()
        unique_instances = []
        for instance in past_instances:
            key = (instance["name"], instance["date"])
            if key not in seen:
                seen.add(key)
                unique_instances.append(instance)

        # Sort by date (most recent first)
        unique_instances.sort(key=lambda x: x["date"], reverse=True)

        # Return the 10 most recent
        top_10 = unique_instances[:10]
        return JSONResponse({"success": True, "events": top_10})

    except Exception as e:
        print(traceback.format_exc())
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/ghost-players")
async def get_ghost_players(
    authenticated: bool = Depends(is_authenticated),
    cache_session: Session = Depends(get_cache_session),
):
    """Gets a list of all discovered ghost players (unmatched OCR detections)."""
    if not authenticated:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    try:
        # Get all unique ghost players (player_fid is None or "0000000000")
        ghost_players_query = (
            select(OCREventData)
            .where(
                (OCREventData.player_fid.is_(None)) | (OCREventData.player_fid == "0000000000")
            )
            .order_by(OCREventData.extracted_at.desc())
        )
        ghost_records = cache_session.exec(ghost_players_query).all()

        # Group by player name to get unique ghost players with stats
        ghost_players_dict = {}
        for record in ghost_records:
            player_name = record.player_name
            if player_name not in ghost_players_dict:
                alliance_tag, username = parse_player_name(player_name)
                ghost_players_dict[player_name] = {
                    "player_name": username,
                    "alliance_tag": alliance_tag,
                    "full_name": player_name,
                    "times_seen": 0,
                    "last_seen_dt": None,
                    "avg_damage": 0,
                    "total_damage": 0,
                    "event_count": 0,
                }

            ghost_players_dict[player_name]["times_seen"] += 1
            if record.damage_points:
                ghost_players_dict[player_name]["total_damage"] += record.damage_points
            if not ghost_players_dict[player_name]["last_seen_dt"] or record.extracted_at > ghost_players_dict[player_name]["last_seen_dt"]:
                ghost_players_dict[player_name]["last_seen_dt"] = record.extracted_at
            ghost_players_dict[player_name]["event_count"] += 1

        # Calculate averages and convert to list
        ghost_players_list = list(ghost_players_dict.values())
        for player in ghost_players_list:
            if player["event_count"] > 0:
                player["avg_damage"] = player["total_damage"] / player["event_count"]
            # Convert datetime to ISO string
            if player["last_seen_dt"]:
                player["last_seen"] = player["last_seen_dt"].isoformat()
            else:
                player["last_seen"] = None
            # Remove the datetime object from response
            del player["last_seen_dt"]

        # Sort by times_seen (most frequent first)
        ghost_players_list.sort(key=lambda x: x["times_seen"], reverse=True)

        return JSONResponse({
            "success": True,
            "total_ghost_players": len(ghost_players_list),
            "ghost_players": ghost_players_list
        })

    except Exception as e:
        print(traceback.format_exc())
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/reconcile-player-names")
async def reconcile_player_names(
    authenticated: bool = Depends(is_authenticated),
    cache_session: Session = Depends(get_cache_session),
    changes_session: Session = Depends(get_changes_session),
    users_session: Session = Depends(get_users_session),
):
    """Reconciles OCR player names with current usernames based on nickname changes."""
    if not authenticated:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    try:
        # Use the helper function to reconcile names
        updated_count, updates_log = reconcile_ocr_player_names(
            cache_session, changes_session, users_session
        )

        return JSONResponse({
            "success": True,
            "updated_count": updated_count,
            "updates": updates_log,
            "message": f"Reconciled {updated_count} OCR records with current player names"
        })

    except Exception as e:
        print(traceback.format_exc())
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/delete-ghost-player")
async def delete_ghost_player(
    authenticated: bool = Depends(is_authenticated),
    cache_session: Session = Depends(get_cache_session),
    data: DeleteGhostPlayerRequest = Body(...),
):
    """Deletes all OCREventData records for a ghost player."""
    if not authenticated:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    try:
        player_name = data.player_name
        # Delete all records for this ghost player
        records_to_delete = cache_session.exec(
            select(OCREventData)
            .where(OCREventData.player_name == player_name)
            .where(
                (OCREventData.player_fid.is_(None)) | (OCREventData.player_fid == "0000000000")
            )
        ).all()

        deleted_count = 0
        for record in records_to_delete:
            cache_session.delete(record)
            deleted_count += 1

        cache_session.commit()

        return JSONResponse({
            "success": True,
            "message": f"Deleted {deleted_count} record(s) for {player_name}",
            "deleted_count": deleted_count
        })

    except Exception as e:
        print(traceback.format_exc())
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/refresh-avatars")
async def refresh_avatars(authenticated: bool = Depends(is_authenticated)):
    """Streams progress updates while fetching and caching avatar URLs."""
    if not authenticated:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    async def generate_progress():
        try:
            with Session(users_engine) as users_session, Session(
                cache_engine
            ) as cache_session:
                # Get all users
                users = users_session.exec(select(User)).all()

                # Track progress
                total_users = len(users)
                updated_count = 0
                skipped_count = 0
                error_count = 0
                current_index = 0

                # Send initial status
                yield f"data: {json.dumps({'type': 'init', 'total': total_users, 'message': f'Starting avatar refresh for {total_users} users...'})}\n\n"

                # Get existing cache entries
                cached_avatars = {}
                cached_entries = cache_session.exec(select(UserAvatarCache)).all()
                for entry in cached_entries:
                    cached_avatars[entry.fid] = entry

                yield f"data: {json.dumps({'type': 'status', 'message': 'Loaded cache entries. Checking which avatars need updating...'})}\n\n"

                # SSL context for API requests
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

                async with aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(ssl=ssl_context)
                ) as session:
                    for user in users:
                        current_index += 1

                        # Check if we need to update this user's avatar
                        cached_entry = cached_avatars.get(user.fid)
                        if cached_entry:
                            # Skip if updated within last 24 hours
                            time_since_update = (
                                datetime.now() - cached_entry.last_updated
                            )
                            if time_since_update < timedelta(hours=24):
                                skipped_count += 1
                                progress = int((current_index / total_users) * 100)
                                yield f"data: {json.dumps({'type': 'progress', 'current': current_index, 'total': total_users, 'percent': progress, 'updated': updated_count, 'skipped': skipped_count, 'errors': error_count, 'message': f'Skipped {user.nickname} (cached recently)'})}\n\n"
                                continue

                        try:
                            # Send status update
                            yield f"data: {json.dumps({'type': 'status', 'message': f'Fetching avatar for {user.nickname}...'})}\n\n"

                            # Prepare API request (same logic as id_channel.py)
                            current_time = int(asyncio.get_event_loop().time() * 1000)
                            form = f"fid={user.fid}&time={current_time}"
                            sign = hashlib.md5(
                                (form + API_SECRET).encode("utf-8")
                            ).hexdigest()
                            form = f"sign={sign}&{form}"
                            headers = {
                                "Content-Type": "application/x-www-form-urlencoded"
                            }

                            # Make API request
                            async with session.post(
                                "https://kingshot-giftcode.centurygame.com/api/player",
                                headers=headers,
                                data=form,
                            ) as response:
                                if response.status == 200:
                                    data = await response.json()
                                    if data.get("data"):
                                        avatar_url = data["data"].get("avatar_image")

                                        # Update or create cache entry
                                        if cached_entry:
                                            cached_entry.avatar_url = avatar_url
                                            cached_entry.last_updated = datetime.now()
                                            cache_session.add(cached_entry)
                                        else:
                                            new_entry = UserAvatarCache(
                                                fid=user.fid,
                                                avatar_url=avatar_url,
                                                last_updated=datetime.now(),
                                                created_at=datetime.now(),
                                            )
                                            cache_session.add(new_entry)

                                        updated_count += 1
                                        cache_session.commit()

                                        progress = int(
                                            (current_index / total_users) * 100
                                        )
                                        yield f"data: {json.dumps({'type': 'progress', 'current': current_index, 'total': total_users, 'percent': progress, 'updated': updated_count, 'skipped': skipped_count, 'errors': error_count, 'message': f'Updated avatar for {user.nickname}'})}\n\n"
                                else:
                                    error_count += 1
                                    progress = int((current_index / total_users) * 100)
                                    yield f"data: {json.dumps({'type': 'progress', 'current': current_index, 'total': total_users, 'percent': progress, 'updated': updated_count, 'skipped': skipped_count, 'errors': error_count, 'message': f'Error fetching {user.nickname} (status {response.status})'})}\n\n"

                            # Rate limiting: wait 2 seconds between requests
                            yield f"data: {json.dumps({'type': 'status', 'message': 'Waiting 2 seconds (rate limit)...'})}\n\n"
                            await asyncio.sleep(2)

                        except Exception as e:
                            error_count += 1
                            progress = int((current_index / total_users) * 100)
                            yield f"data: {json.dumps({'type': 'progress', 'current': current_index, 'total': total_users, 'percent': progress, 'updated': updated_count, 'skipped': skipped_count, 'errors': error_count, 'message': f'Error processing {user.nickname}: {str(e)}'})}\n\n"
                            continue

                # Send completion message
                yield f"data: {json.dumps({'type': 'complete', 'updated': updated_count, 'skipped': skipped_count, 'errors': error_count, 'message': 'Avatar refresh completed!'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(generate_progress(), media_type="text/event-stream")


@router.get("/api/thumbnails")
async def get_thumbnails(authenticated: bool = Depends(is_authenticated)):
    """Gets a list of available thumbnail images."""
    if not authenticated:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    thumbnails_dir = os.path.join("web", "static", "images", "thumbnails")
    thumbnails = []

    if os.path.exists(thumbnails_dir):
        for filename in os.listdir(thumbnails_dir):
            if filename.lower().endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
            ):
                thumbnails.append(
                    {
                        "filename": filename,
                        "url": f"/static/images/thumbnails/{filename}",
                    }
                )

    # Sort alphabetically
    thumbnails.sort(key=lambda x: x["filename"].lower())

    return JSONResponse(content={"thumbnails": thumbnails})


@router.post("/create_event", response_class=JSONResponse)
async def create_event(
    authenticated: bool = Depends(is_authenticated),
    beartime_session: Session = Depends(get_beartime_session),
    data: EventCreate = Body(...),
):
    """Creates a new event."""
    if not authenticated:
        return JSONResponse(
            content={"success": False, "error": "Unauthorized"}, status_code=401
        )

    try:
        full_description = ""
        if data.notification_type == 6 and data.custom_times:
            full_description = f"{CUSTOM_TIMES_PREFIX}{data.custom_times}|"

        if data.message_type == "embed":
            full_description += EMBED_MESSAGE_MARKER
        elif data.message_type == "plain":
            full_description += f"{PLAIN_MESSAGE_PREFIX}{data.description}"

        if data.next_notification.tzinfo is None:
            aware_dt = pytz.utc.localize(data.next_notification)
        else:
            aware_dt = data.next_notification

        new_notification = BearNotification(
            guild_id=1,
            channel_id=int(data.channel_id),
            hour=data.hour,
            minute=data.minute,
            timezone=data.timezone,
            description=full_description,
            notification_type=data.notification_type,
            mention_type=data.mention_type,
            repeat_enabled=(
                1 if data.repeat_minutes and data.repeat_minutes != "0" else 0
            ),
            repeat_minutes=data.repeat_minutes,
            is_enabled=1 if data.is_enabled else 0,
            created_by=0,
            next_notification=aware_dt,  # Pass datetime object directly
        )
        beartime_session.add(new_notification)
        beartime_session.flush()

        if data.message_type == "embed":
            new_embed = BearNotificationEmbed(
                notification_id=new_notification.id,
                title=data.embed_title,
                description=data.embed_description,
                color=(
                    int(data.embed_color.lstrip("#"), 16) if data.embed_color else None
                ),
                image_url=data.embed_image_url,
                thumbnail_url=data.embed_thumbnail_url,
                footer=data.embed_footer,
                author=data.embed_author,
                mention_message=(
                    data.embed_mention_message if data.embed_mention_message else None
                ),
            )
            beartime_session.add(new_embed)

        if data.repeat_minutes == REPEAT_TYPE_FIXED and data.weekdays:
            sorted_days = sorted(data.weekdays)
            weekday_str = "|".join(map(str, sorted_days))
            new_notification_days = NotificationDays(
                notification_id=new_notification.id, weekday=weekday_str
            )
            beartime_session.add(new_notification_days)

        beartime_session.commit()
        return JSONResponse(content={"success": True, "id": new_notification.id})

    except Exception as e:
        return JSONResponse(
            content={"success": False, "error": str(e)}, status_code=500
        )


@router.post("/update_event", response_class=JSONResponse)
async def update_event(
    authenticated: bool = Depends(is_authenticated),
    beartime_session: Session = Depends(get_beartime_session),
    data: EventUpdate = Body(...),
):
    """Updates an existing event."""
    if not authenticated:
        return JSONResponse(
            content={"success": False, "error": "Unauthorized"}, status_code=401
        )

    notification = beartime_session.get(BearNotification, data.id)
    if not notification:
        return JSONResponse(
            content={"success": False, "error": "Event not found"}, status_code=404
        )

    full_description = ""
    if data.notification_type == 6 and data.custom_times:
        full_description = f"CUSTOM_TIMES:{data.custom_times}|"

    if data.message_type == "embed":
        full_description += "EMBED_MESSAGE:true"
    elif data.message_type == "plain":
        full_description += f"PLAIN_MESSAGE:{data.description}"
    notification.description = full_description

    notification.channel_id = int(data.channel_id)
    notification.hour = data.hour
    notification.minute = data.minute
    notification.repeat_minutes = data.repeat_minutes
    notification.mention_type = data.mention_type or "none"
    notification.notification_type = data.notification_type
    notification.repeat_enabled = (
        1 if data.repeat_minutes and data.repeat_minutes != "0" else 0
    )
    notification.is_enabled = 1 if data.is_enabled else 0

    if data.next_notification.tzinfo is None:
        aware_dt = pytz.utc.localize(data.next_notification)
    else:
        aware_dt = data.next_notification
    notification.next_notification = aware_dt

    if data.message_type == "embed":
        if not notification.embeds:
            embed = BearNotificationEmbed(notification_id=notification.id)
            beartime_session.add(embed)
        else:
            embed = notification.embeds[0]

        embed.title = data.embed_title
        embed.description = data.embed_description
        embed.color = (
            int(data.embed_color.lstrip("#"), 16) if data.embed_color else None
        )
        embed.footer = data.embed_footer
        embed.author = data.author
        embed.image_url = data.image_url
        embed.thumbnail_url = data.thumbnail_url
        embed.mention_message = (
            data.embed_mention_message if data.embed_mention_message else None
        )

    if data.repeat_minutes == REPEAT_TYPE_FIXED:
        if not notification.notification_days:
            new_notification_days = NotificationDays(
                notification_id=notification.id,
                weekday="|".join(map(str, sorted(data.weekdays))),
            )
            beartime_session.add(new_notification_days)
        elif data.weekdays:
            notification.notification_days.weekday = "|".join(
                map(str, sorted(data.weekdays))
            )
    elif notification.notification_days:
        beartime_session.delete(notification.notification_days)

    beartime_session.add(notification)
    beartime_session.commit()
    beartime_session.refresh(notification)

    return JSONResponse(content={"success": True})
