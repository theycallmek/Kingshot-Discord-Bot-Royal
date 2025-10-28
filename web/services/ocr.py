"""
OCR processing services for the web application.

This module contains functions for initializing the OCR reader, preprocessing
images, extracting data from OCR results, and matching player scores.
"""

import os
import re
import cv2
import numpy as np
from pathlib import Path
from paddleocr import PaddleOCR
from sqlmodel import Session, select
from datetime import datetime, timedelta

from web.models import User, AttendanceRecord
from web.ocr_models import OCRPlayerMapping, OCREventData

# Initialize PaddleOCR reader (loaded once at startup)
ocr_reader = None

def get_ocr_reader():
    """
    Initializes and returns a singleton PaddleOCR reader instance.
    """
    global ocr_reader
    if ocr_reader is None:
        ocr_reader = PaddleOCR(lang='en')
    return ocr_reader

def preprocess_image_for_ocr(image_path: str) -> str:
    """
    Preprocesses an image for improved OCR accuracy using adaptive thresholding.

    Args:
        image_path: The path to the image file.

    Returns:
        The path to the preprocessed image.
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")
    try:
        with open(image_path, 'rb') as f:
            file_bytes = np.frombuffer(f.read(), dtype=np.uint8)
        image = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Could not decode image from path: {image_path}")
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY, 11, 2)
        result = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)
        ext = os.path.splitext(image_path)[1] or '.png'
        is_success, buffer = cv2.imencode(ext, result)
        if not is_success:
            raise ValueError(f"Could not encode preprocessed image: {image_path}")
        with open(image_path, 'wb') as f:
            f.write(buffer)
    except Exception as e:
        raise ValueError(f"Failed to preprocess image at {image_path}: {e}") from e
    return image_path

def extract_player_scores_from_ocr(ocr_results: list, image_name: str) -> list:
    """
    Extracts player names, rankings, and scores from PaddleOCR results.

    Args:
        ocr_results: The raw results from the PaddleOCR engine.
        image_name: The name of the image file processed.

    Returns:
        A list of dictionaries, each containing data for a detected player.
    """
    player_data = []
    if not ocr_results or not isinstance(ocr_results, list) or len(ocr_results) == 0:
        return player_data
    page_result = ocr_results[0]
    if 'rec_texts' not in page_result or 'rec_scores' not in page_result:
        return player_data
    texts = page_result['rec_texts']
    scores = page_result['rec_scores']
    polys = page_result.get('rec_polys', [])
    text_items = []
    for i, (text, confidence) in enumerate(zip(texts, scores)):
        poly = polys[i] if i < len(polys) else None
        text_items.append((text, confidence, poly))
    for i, (text, confidence, poly) in enumerate(text_items):
        player_name = text.strip()
        if player_name.startswith('[DOAJ'):
            player_name = '[DOA]' + player_name[5:]
        elif player_name.startswith('[DOA') and len(player_name) > 4 and player_name[4] != ']':
            player_name = '[DOA]' + player_name[4:]
        if player_name.startswith('[DOA]') and confidence > 0.55:
            try:
                full_player_name = player_name
                if poly is not None and len(poly) > 0:
                    try:
                        player_y = float(poly[:, 1].min())
                        player_x_end = float(poly[:, 0].max())
                        for t, c, p in text_items:
                            if p is not None and len(p) > 0 and t != text:
                                try:
                                    text_y = float(p[:, 1].min())
                                    text_x_start = float(p[:, 0].min())
                                    if abs(text_y - player_y) < 30 and text_x_start >= player_x_end - 10 and text_x_start < player_x_end + 200:
                                        if not t.isdigit() and 'damage' not in t.lower() and 'point' not in t.lower():
                                            full_player_name += " " + t
                                            break
                                except (ValueError, TypeError):
                                    continue
                    except (ValueError, TypeError, IndexError):
                        pass
                player_name = full_player_name
                ranking = None
                if poly is not None and len(poly) > 0:
                    try:
                        player_y = float(poly[:, 1].min())
                        player_x = float(poly[:, 0].min())
                        for t, c, p in text_items:
                            if p is not None and len(p) > 0 and t.isdigit():
                                try:
                                    if 1 <= int(t) <= 50:
                                        text_y = float(p[:, 1].min())
                                        text_x_max = float(p[:, 0].max())
                                        if abs(text_y - player_y) < 50 and text_x_max < player_x:
                                            ranking = int(t)
                                            break
                                except (ValueError, TypeError):
                                    continue
                    except (ValueError, TypeError, IndexError):
                        pass
                damage_points = None
                if poly is not None and len(poly) > 0:
                    try:
                        player_y_bottom = float(poly[:, 1].max())
                        for t, c, p in text_items:
                            if p is not None and len(p) > 0:
                                try:
                                    text_y_top = float(p[:, 1].min())
                                    if text_y_top > player_y_bottom and text_y_top < player_y_bottom + 100:
                                        if 'damage' in t.lower() and ('point' in t.lower() or ':' in t):
                                            numbers = re.findall(r'[\d,]+', t)
                                            if numbers:
                                                try:
                                                    damage_points = int(numbers[-1].replace(',', ''))
                                                except (ValueError, TypeError):
                                                    pass
                                            break
                                except (ValueError, TypeError, IndexError):
                                    continue
                    except (ValueError, TypeError, IndexError):
                        pass
                player_data.append({
                    'player_name': player_name,
                    'raw_name': text,
                    'ranking': ranking,
                    'damage_points': damage_points,
                    'confidence': confidence,
                    'image_source': image_name
                })
            except Exception as e:
                print(f"[ERROR] Failed to process {player_name}: {e}")
    return player_data

def match_and_store_scores(
    player_data_list: list,
    session_id: str,
    event_name: str,
    event_type: str,
    event_date: datetime,
    user_session: Session,
    cache_session: Session
) -> tuple:
    """
    Matches OCR-extracted player data with database users and stores the results.

    Args:
        player_data_list: A list of player data dictionaries from OCR.
        session_id: A unique identifier for the current processing session.
        event_name: The name of the event.
        event_type: The type of the event.
        event_date: The date and time of the event.
        user_session: An active session for the users database.
        cache_session: An active session for the cache database.

    Returns:
        A tuple containing (matched_count, matched_players, unmatched_players).
    """
    matched_players = []
    unmatched = []
    all_users = user_session.exec(select(User)).all()
    users_by_nickname = {re.sub(r'\[.*?\]', '', user.nickname).strip().lower(): user for user in all_users}

    for player_data in player_data_list:
        player_name = player_data['player_name']
        matched = False
        player_fid = "0000000000"
        user_obj = None
        extracted_nickname = re.sub(r'\[.*?\]', '', player_name).strip()
        if extracted_nickname.lower() in users_by_nickname:
            user_obj = users_by_nickname[extracted_nickname.lower()]
            player_fid = str(user_obj.fid)
            matched = True

        existing_mapping = cache_session.exec(
            select(OCRPlayerMapping)
            .where(OCRPlayerMapping.player_name == player_name)
        ).first()

        if existing_mapping:
            existing_mapping.last_seen = datetime.now()
            existing_mapping.times_seen += 1
            existing_mapping.updated_at = datetime.now()
            if matched and existing_mapping.player_fid == "0000000000":
                existing_mapping.player_fid = player_fid
                existing_mapping.confidence = player_data['confidence']
            cache_session.add(existing_mapping)
        else:
            mapping = OCRPlayerMapping(
                player_name=player_name,
                player_fid=player_fid,
                confidence=player_data['confidence'],
                first_seen=datetime.now(),
                last_seen=datetime.now(),
                times_seen=1,
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
            cache_session.add(mapping)

        event_date_str = event_date.strftime('%Y-%m-%d')
        existing_event = cache_session.exec(
            select(OCREventData)
            .where(OCREventData.event_name == event_name)
            .where(OCREventData.player_name == player_name)
            .where(OCREventData.event_date >= datetime.strptime(event_date_str, '%Y-%m-%d'))
            .where(OCREventData.event_date < datetime.strptime(event_date_str, '%Y-%m-%d') + timedelta(days=1))
        ).first()

        if not existing_event:
            event_record = OCREventData(
                event_name=event_name,
                event_type=event_type,
                event_date=event_date,
                player_name=player_name,
                player_fid=player_fid if matched else None,
                ranking=player_data.get('ranking'),
                rank_inferred=False,
                score=None,
                damage_points=player_data.get('damage_points'),
                time_value=None,
                ocr_confidence=player_data['confidence'],
                image_source=player_data['image_source'],
                processing_session=session_id,
                verification_count=1,
                verified_sessions=session_id,
                data_confidence=1.0,
                extracted_at=datetime.now(),
                created_at=datetime.now()
            )
            cache_session.add(event_record)
        else:
            new_damage = player_data.get('damage_points', 0)
            new_confidence = player_data['confidence']
            new_ranking = player_data.get('ranking')
            should_update = False

            verified_sessions_list = existing_event.verified_sessions.split(',') if existing_event.verified_sessions else []
            is_new_verification = session_id not in verified_sessions_list

            damage_tolerance = 1000
            damage_matches = False
            if new_damage and existing_event.damage_points:
                damage_diff = abs(new_damage - existing_event.damage_points)
                damage_matches = damage_diff <= damage_tolerance

            ranking_matches = new_ranking == existing_event.ranking if new_ranking and existing_event.ranking else False

            if is_new_verification and (damage_matches or ranking_matches):
                verified_sessions_list.append(session_id)
                existing_event.verified_sessions = ','.join(verified_sessions_list)
                existing_event.verification_count = len(verified_sessions_list)
                existing_event.data_confidence = min(2.0, 1.0 + (0.2 * (existing_event.verification_count - 1)))
                should_update = True

            if new_damage and new_damage > (existing_event.damage_points or 0):
                existing_event.damage_points = new_damage
                existing_event.processing_session = session_id
                should_update = True

            if new_ranking and not existing_event.ranking:
                existing_event.ranking = new_ranking
                should_update = True

            if new_ranking and existing_event.ranking and new_ranking < existing_event.ranking:
                existing_event.ranking = new_ranking
                should_update = True

            if new_confidence > existing_event.ocr_confidence:
                existing_event.ocr_confidence = new_confidence
                existing_event.image_source = player_data['image_source']
                should_update = True

            if should_update:
                cache_session.add(existing_event)

        if matched:
            player_data['player_fid'] = player_fid
            player_data['user'] = user_obj
            matched_players.append(player_data)
        else:
            player_data['player_fid'] = player_fid
            unmatched.append(player_data)

    return len(matched_players), matched_players, unmatched

def mark_attendance_from_scores(matched_players, unmatched_players, event_name, ocr_session_id, attendance_session: Session):
    """
    Mark attendance for matched players only (unmatched players are tracked in OCR cache).
    Handles duplicates by keeping the highest damage score when a player appears multiple times.
    """
    event_date = datetime.now()
    session_id = f"{event_name}_{event_date.strftime('%Y%m%d')}"
    marked_count = 0
    updated_count = 0

    with attendance_session as att_session:
        # Mark attendance for matched players (real users)
        for player_data in matched_players:
            user = player_data['user']
            new_damage = player_data.get('damage_points') or 0

            # Check if already exists
            existing = att_session.exec(
                select(AttendanceRecord)
                .where(AttendanceRecord.player_id == str(user.fid))
                .where(AttendanceRecord.session_id == session_id)
            ).first()

            if not existing:
                # Create new attendance record
                attendance = AttendanceRecord(
                    session_id=session_id,
                    session_name=event_name,
                    event_type="OCR Import",
                    event_date=event_date,
                    player_id=str(user.fid),
                    player_name=user.nickname,
                    alliance_id=str(user.alliance),
                    alliance_name=f"Alliance {user.alliance}",
                    status="present",
                    points=new_damage,
                    marked_at=datetime.now(),
                    marked_by="OCR_System",
                    marked_by_username="Automated OCR",
                    created_at=datetime.now()
                )
                att_session.add(attendance)
                marked_count += 1
            else:
                # Update if new damage score is higher (player appeared in multiple screenshots)
                if new_damage and new_damage > (existing.points or 0):
                    existing.points = new_damage
                    existing.marked_at = datetime.now()  # Update timestamp
                    att_session.add(existing)
                    updated_count += 1

        att_session.commit()

    # Return total marked (new + updated)
    return marked_count + updated_count
