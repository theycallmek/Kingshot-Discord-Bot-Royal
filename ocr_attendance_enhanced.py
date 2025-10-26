"""
Enhanced OCR Attendance System with Caching
Processes event screenshots, extracts scores, and stores all data in cache
"""

import easyocr
import os
from pathlib import Path
import time
from datetime import datetime
from sqlmodel import Session, select, create_engine, SQLModel
from web.models import User, AttendanceRecord
from web.ocr_models import OCRPlayerMapping, OCREventData
from web.ocr_name_correction import NameCorrectionEngine
import re

# Database Setup
DB_DIR = os.path.abspath("db")
os.makedirs(DB_DIR, exist_ok=True)
users_engine = create_engine(f"sqlite:///{os.path.join(DB_DIR, 'users.sqlite')}", connect_args={"check_same_thread": False})
attendance_engine = create_engine(f"sqlite:///{os.path.join(DB_DIR, 'attendance.sqlite')}", connect_args={"check_same_thread": False})
cache_engine = create_engine(f"sqlite:///{os.path.join(DB_DIR, 'web_cache.sqlite')}", connect_args={"check_same_thread": False})

# Create OCR tables in cache database
SQLModel.metadata.create_all(cache_engine)


class EnhancedOCRProcessor:
    def __init__(self):
        """Initialize EasyOCR reader"""
        print("Initializing Enhanced OCR reader...")
        self.reader = easyocr.Reader(['en'], gpu=False, verbose=False)
        print("OCR reader ready!")
        self.current_session_id = None
        self.event_name = None
        self.event_type = None
        self.event_date = None

        # Initialize name correction engine
        print("Initializing name correction engine...")
        self.name_corrector = NameCorrectionEngine(users_engine)
        print("Name correction engine ready!")

    def create_session(self, event_name, event_type, event_date=None):
        """Create a new OCR processing session"""
        if event_date is None:
            event_date = datetime.now()

        # Store session info for later use
        self.event_name = event_name
        self.event_type = event_type
        self.event_date = event_date
        self.current_session_id = f"{event_name}_{event_date.strftime('%Y%m%d_%H%M%S')}"

        print(f"Created OCR Session ID: {self.current_session_id}")
        return self.current_session_id

    def extract_all_data(self, image_path):
        """Extract all text and data from screenshot"""
        print(f"\nProcessing: {os.path.basename(image_path)}")

        # Read all text from image
        # Using mostly default parameters - let the name correction handle cleanup
        # Only setting paragraph=False for better structured data detection
        results = self.reader.readtext(
            image_path,
            paragraph=False
        )

        # Extract structured data
        image_name = os.path.basename(image_path)
        player_data = self.extract_player_scores(results, image_name)

        return player_data

    def extract_player_scores(self, ocr_results, image_name):
        """Extract player names, rankings, and scores from OCR results"""
        player_data = []

        # Create a list of all detected text with positions
        texts = [(bbox, text, confidence) for (bbox, text, confidence) in ocr_results]

        # Find the image height to detect sticky bottom card
        # The sticky card is typically in the bottom 20% of the image
        max_y = max(bbox[2][1] for bbox, _, _ in texts) if texts else 0
        sticky_threshold = max_y * 0.80  # Bottom 20% of image

        # Look for patterns
        for i, (bbox, text, confidence) in enumerate(texts):
            # Check if this looks like a player name (starts with [ and contains alliance tag)
            if text.startswith('[') and confidence > 0.6:
                player_name = self.clean_player_name(text)

                # Skip if name doesn't match alliance tag format after cleaning
                if not self.is_valid_alliance_name_format(player_name):
                    print(f"  Skipping invalid format: {text}")
                    continue

                # Check if this is the sticky bottom card (skip it to avoid duplicates)
                player_bottom_y = bbox[2][1]
                is_sticky_card = player_bottom_y > sticky_threshold

                # Try to find associated ranking (usually above or to the left)
                ranking = self.find_nearby_ranking(texts, i, bbox)

                # Try to find associated damage points (usually below)
                damage_points = self.find_nearby_damage_score(texts, i, bbox)

                player_entry = {
                    'player_name': player_name,
                    'raw_name': text,
                    'ranking': ranking,
                    'damage_points': damage_points,
                    'confidence': confidence,
                    'image_source': image_name,
                    'bbox_y': bbox[0][1],  # Store Y position for sorting
                    'is_sticky_card': is_sticky_card
                }

                player_data.append(player_entry)

                status = "[STICKY]" if is_sticky_card else "[RANKED]"
                print(f"  {status} Found: {player_name}")
                if ranking:
                    print(f"    Rank: {ranking}")
                if damage_points:
                    print(f"    Damage: {damage_points:,}")

        return player_data

    def find_nearby_ranking(self, texts, player_index, player_bbox):
        """Find ranking number near a player name"""
        player_y = player_bbox[0][1]  # Y coordinate of player name
        player_x = player_bbox[0][0]  # X coordinate

        # Look for numbers 1-50 nearby (rankings)
        for bbox, text, conf in texts:
            if text.isdigit() and 1 <= int(text) <= 50:
                text_y = bbox[0][1]
                text_x = bbox[0][0]

                # Check if it's roughly on the same line and to the left
                if abs(text_y - player_y) < 50 and text_x < player_x:
                    return int(text)

        return None

    def find_nearby_damage_score(self, texts, player_index, player_bbox):
        """Find damage points score near a player name"""
        player_y = player_bbox[2][1]  # Bottom Y coordinate of player name

        # Look for "Damage Points:123,456,789" pattern below player name
        for bbox, text, conf in texts:
            text_y = bbox[0][1]

            # Check if it's below the player name
            if text_y > player_y and text_y < player_y + 100:
                # Check if it contains "Damage Point" and numbers
                if 'damage point' in text.lower():
                    # Extract numbers
                    numbers = re.findall(r'[\d,]+', text)
                    if numbers:
                        # Remove commas and convert to int
                        try:
                            damage = int(numbers[-1].replace(',', ''))
                            return damage
                        except:
                            pass

        return None

    def is_valid_alliance_name_format(self, name):
        """
        Validate that name matches alliance tag format: [TAG]Nickname
        Tag must be 2-4 characters, followed by a name
        """
        # Pattern: [2-4 chars]Name
        pattern = r'^\[[A-Za-z0-9]{2,4}\][A-Za-z0-9]'
        return bool(re.match(pattern, name))

    def clean_player_name(self, raw_name):
        """
        No cleanup - return raw OCR output as-is
        """
        return raw_name.strip()

    def infer_missing_ranks(self, player_data_list):
        """
        Infer missing ranks based on adjacent players with known ranks.
        Players are ordered from rank 1 (top) to worst rank (bottom).
        """
        # Sort by image and vertical position if bbox data is available
        sorted_players = sorted(player_data_list, key=lambda x: (
            x['image_source'],
            x.get('bbox_y', 0)  # Will add bbox_y in extract method
        ))

        for i, player in enumerate(sorted_players):
            if player.get('ranking') is None:
                # Try to infer from adjacent players
                inferred_rank = None
                inference_confidence = 0.0

                # Look at player above
                if i > 0:
                    prev_player = sorted_players[i - 1]
                    if prev_player.get('ranking') is not None:
                        # Current player should be previous rank + 1
                        inferred_rank = prev_player['ranking'] + 1
                        inference_confidence = 0.8

                # Look at player below if we haven't found rank yet
                if inferred_rank is None and i < len(sorted_players) - 1:
                    next_player = sorted_players[i + 1]
                    if next_player.get('ranking') is not None:
                        # Current player should be next rank - 1
                        inferred_rank = next_player['ranking'] - 1
                        if inferred_rank >= 1:
                            inference_confidence = 0.8
                        else:
                            inferred_rank = None

                # Apply inferred rank if found
                if inferred_rank is not None:
                    player['ranking'] = inferred_rank
                    player['rank_inferred'] = True
                    player['rank_inference_confidence'] = inference_confidence
                    print(f"  Inferred rank {inferred_rank} for {player['player_name']} (confidence: {inference_confidence:.1%})")
                else:
                    player['rank_inferred'] = False

        return sorted_players

    def match_and_store_scores(self, player_data_list, alliance_id=None):
        """Match players and store scores in cache"""
        matched_count = 0
        unmatched = []
        corrections_made = 0

        # First, infer missing ranks
        print("\nInferring missing ranks from adjacent players...")
        player_data_list = self.infer_missing_ranks(player_data_list)

        # Apply intelligent name correction
        print("\nApplying intelligent name correction...")

        with Session(users_engine) as user_session, Session(cache_engine) as cache_session:
            for player_data in player_data_list:
                original_name = player_data['player_name']
                matched = False
                player_fid = None
                match_confidence = player_data['confidence']

                # Use name correction engine
                corrected_name, matched_user, correction_confidence, debug_info = self.name_corrector.correct_player_name(
                    original_name
                )

                # ALWAYS apply the corrected name (fixes bracket errors even if no match)
                if corrected_name != original_name:
                    corrections_made += 1
                    print(f"  [CORRECTED] {original_name} -> {corrected_name}")
                    player_data['player_name'] = corrected_name
                    player_data['original_ocr_name'] = original_name
                    player_data['name_corrected'] = True

                if matched_user:
                    # Successfully matched to a user in database
                    player_fid = str(matched_user.fid)
                    matched = True
                    matched_count += 1
                    print(f"  [MATCHED] {corrected_name} -> FID: {player_fid} (confidence: {correction_confidence:.1%})")

                    # Combine OCR confidence with correction confidence
                    match_confidence = player_data['confidence'] * correction_confidence
                else:
                    # No match found in database, but still use corrected name
                    unmatched.append(player_data)
                    player_fid = "0000000000"  # Placeholder for unmatched
                    print(f"  [UNMATCHED] {corrected_name} - No database match found (ghost player)")

                # Update or create player mapping (use corrected name)
                corrected_player_name = player_data['player_name']  # This is the corrected name
                existing_mapping = cache_session.exec(
                    select(OCRPlayerMapping)
                    .where(OCRPlayerMapping.player_name == corrected_player_name)
                ).first()

                if existing_mapping:
                    existing_mapping.last_seen = datetime.now()
                    existing_mapping.times_seen += 1
                    existing_mapping.updated_at = datetime.now()
                    # Update FID if we have a match and it was previously unmatched
                    if matched and existing_mapping.player_fid == "0000000000":
                        existing_mapping.player_fid = player_fid
                        existing_mapping.confidence = match_confidence
                    cache_session.add(existing_mapping)
                else:
                    mapping = OCRPlayerMapping(
                        player_name=corrected_player_name,  # Use corrected name
                        player_fid=player_fid,
                        confidence=match_confidence,
                        first_seen=datetime.now(),
                        last_seen=datetime.now(),
                        times_seen=1,
                        created_at=datetime.now(),
                        updated_at=datetime.now()
                    )
                    cache_session.add(mapping)

                # Store event data (use corrected name)
                event_record = OCREventData(
                    event_name=self.event_name,
                    event_type=self.event_type,
                    event_date=self.event_date,
                    player_name=corrected_player_name,  # Use corrected name
                    player_fid=player_fid if matched else None,
                    ranking=player_data.get('ranking'),
                    rank_inferred=player_data.get('rank_inferred', False),
                    score=None,  # Generic score field for future use
                    damage_points=player_data.get('damage_points'),
                    time_value=None,  # For time-based events
                    ocr_confidence=player_data['confidence'],
                    image_source=player_data['image_source'],
                    processing_session=self.current_session_id,
                    extracted_at=datetime.now(),
                    created_at=datetime.now()
                )
                cache_session.add(event_record)

            cache_session.commit()

        print(f"\n  Total corrections made: {corrections_made}")
        return matched_count, unmatched, corrections_made

    def mark_attendance(self, event_name, event_date=None):
        """Mark attendance for all matched players from current session"""
        if event_date is None:
            event_date = datetime.now()

        session_id = f"{event_name}_{event_date.strftime('%Y%m%d')}"
        marked_count = 0

        with Session(cache_engine) as cache_session, Session(attendance_engine) as att_session:
            # Get all matched players from this OCR session
            matched_players = cache_session.exec(
                select(OCREventData)
                .where(OCREventData.processing_session == self.current_session_id)
                .where(OCREventData.player_fid != None)
                .where(OCREventData.player_fid != "0000000000")
            ).all()

            for event_data in matched_players:
                # Check if attendance already exists
                existing = att_session.exec(
                    select(AttendanceRecord)
                    .where(AttendanceRecord.player_id == event_data.player_fid)
                    .where(AttendanceRecord.session_id == session_id)
                ).first()

                if existing:
                    print(f"  Attendance already recorded for {event_data.player_name}")
                else:
                    # Get user details
                    with Session(users_engine) as user_session:
                        user = user_session.exec(
                            select(User).where(User.fid == int(event_data.player_fid))
                        ).first()

                        if user:
                            attendance = AttendanceRecord(
                                session_id=session_id,
                                session_name=event_name,
                                event_type="OCR Import",
                                event_date=event_date,
                                player_id=event_data.player_fid,
                                player_name=event_data.player_name,
                                alliance_id=str(user.alliance),
                                alliance_name=f"Alliance {user.alliance}",
                                status="present",
                                points=event_data.damage_points or 0,
                                marked_at=datetime.now(),
                                marked_by="OCR_System",
                                marked_by_username="Automated OCR",
                                created_at=datetime.now()
                            )
                            att_session.add(attendance)
                            marked_count += 1
                            print(f"  Marked attendance for {event_data.player_name}")

            att_session.commit()

        return marked_count

    def generate_report(self, unmatched_names, total_images):
        """Generate comprehensive report"""
        with Session(cache_engine) as session:
            # Get all event data from this session
            all_events = session.exec(
                select(OCREventData)
                .where(OCREventData.processing_session == self.current_session_id)
            ).all()

            matched = [e for e in all_events if e.player_fid and e.player_fid != "0000000000"]
            unmatched = [e for e in all_events if not e.player_fid or e.player_fid == "0000000000"]

            print("\n" + "="*60)
            print("ENHANCED OCR ATTENDANCE REPORT")
            print("="*60)

            print(f"\nEvent: {self.event_name}")
            print(f"Event Type: {self.event_type}")
            print(f"Date: {self.event_date.strftime('%Y-%m-%d')}")
            print(f"\nImages Processed: {total_images}")
            print(f"Total Players Detected: {len(all_events)}")
            print(f"Successfully Matched: {len(matched)}")
            print(f"Unable to Match: {len(unmatched)}")

            if matched:
                print("\n--- MATCHED PLAYERS (WITH SCORES) ---")
                # Sort by ranking if available
                sorted_matched = sorted(matched, key=lambda x: x.ranking if x.ranking else 999)
                for event_data in sorted_matched:
                    rank_str = f"Rank {event_data.ranking}" if event_data.ranking else "Rank: N/A"
                    if event_data.rank_inferred:
                        rank_str += " (inferred)"
                    damage_str = f"{event_data.damage_points:,}" if event_data.damage_points else "N/A"
                    print(f"  {event_data.player_name}")
                    print(f"    {rank_str} | Damage: {damage_str} | FID: {event_data.player_fid} | Confidence: {event_data.ocr_confidence:.1%}")

            if unmatched:
                print("\n--- UNMATCHED NAMES (Need Manual Review) ---")
                for event_data in unmatched:
                    rank_str = f"Rank {event_data.ranking}" if event_data.ranking else "Rank: N/A"
                    if event_data.rank_inferred:
                        rank_str += " (inferred)"
                    damage_str = f"{event_data.damage_points:,}" if event_data.damage_points else "N/A"
                    print(f"  {event_data.player_name}")
                    print(f"    {rank_str} | Damage: {damage_str} | Confidence: {event_data.ocr_confidence:.1%}")

            print("\n" + "="*60)


def process_event_screenshots(image_folder, event_name, event_type="Damage Rewards", alliance_id=None):
    """
    Main function to process event screenshots with full data extraction

    Args:
        image_folder: Path to folder containing screenshots
        event_name: Name of the event
        event_type: Type of event (e.g., "Damage Rewards", "Bear Trap")
        alliance_id: Optional alliance ID to filter users
    """
    processor = EnhancedOCRProcessor()

    # Create processing session
    session_id = processor.create_session(event_name, event_type)
    print(f"Created OCR Session ID: {session_id}")

    # Get all images
    folder = Path(image_folder)
    image_files = list(folder.glob('*.png')) + list(folder.glob('*.jpg')) + list(folder.glob('*.jpeg'))

    if not image_files:
        print(f"No images found in {image_folder}")
        return

    print(f"\nFound {len(image_files)} image(s) to process")

    # Process all images
    all_player_data = []
    for image_path in image_files:
        player_data = processor.extract_all_data(str(image_path))
        all_player_data.extend(player_data)

    # Remove duplicates using fuzzy matching on name, rank, and score
    unique_players = {}
    sticky_cards_removed = 0
    fuzzy_duplicates_removed = 0

    def is_fuzzy_duplicate(data1, data2):
        """
        Check if two player entries are likely duplicates based on:
        - Similar names (fuzzy match)
        - Same or adjacent rank
        - Similar damage scores (off by 1-2 digits due to OCR misreads)
        """
        name1 = data1['player_name'].lower()
        name2 = data2['player_name'].lower()

        # Name similarity (at least 85% similar)
        from rapidfuzz import fuzz
        name_similarity = fuzz.ratio(name1, name2)

        # Check rank similarity
        rank1 = data1.get('ranking')
        rank2 = data2.get('ranking')
        rank_match = False
        if rank1 and rank2:
            # Same rank or within 1 position
            rank_match = abs(rank1 - rank2) <= 1

        # Check damage score similarity
        damage1 = data1.get('damage_points')
        damage2 = data2.get('damage_points')
        score_match = False
        if damage1 and damage2:
            # Calculate percentage difference
            max_damage = max(damage1, damage2)
            if max_damage > 0:
                diff_percentage = abs(damage1 - damage2) / max_damage * 100
                # If scores differ by less than 1% (allows for 1-2 digit OCR errors)
                score_match = diff_percentage < 1.0

        # Determine if duplicate
        if name_similarity >= 95:
            # Very similar names - likely duplicate
            return True
        elif name_similarity >= 85 and (rank_match or score_match):
            # Similar name + matching rank or score
            return True
        elif rank_match and score_match and name_similarity >= 70:
            # Same rank + same score + somewhat similar name
            return True

        return False

    for data in all_player_data:
        name = data['player_name']
        is_sticky = data.get('is_sticky_card', False)

        # Check for exact name match first
        if name in unique_players:
            existing = unique_players[name]
            existing_is_sticky = existing.get('is_sticky_card', False)

            # Prioritize ranked cards over sticky cards
            if is_sticky and not existing_is_sticky:
                sticky_cards_removed += 1
                continue
            elif not is_sticky and existing_is_sticky:
                unique_players[name] = data
                sticky_cards_removed += 1
            elif data['confidence'] > existing['confidence']:
                unique_players[name] = data
            continue

        # Check for fuzzy duplicates (name slightly different due to OCR errors)
        found_duplicate = False
        for existing_name, existing_data in unique_players.items():
            if is_fuzzy_duplicate(data, existing_data):
                # Found a fuzzy duplicate
                existing_is_sticky = existing_data.get('is_sticky_card', False)

                print(f"  [FUZZY DUPLICATE] '{data['player_name']}' matches existing '{existing_name}'")
                print(f"    Rank: {data.get('ranking')} vs {existing_data.get('ranking')}")
                print(f"    Damage: {data.get('damage_points'):,} vs {existing_data.get('damage_points'):,}")

                # Decide which to keep
                if is_sticky and not existing_is_sticky:
                    # Keep existing ranked card
                    fuzzy_duplicates_removed += 1
                elif not is_sticky and existing_is_sticky:
                    # Replace with ranked card
                    del unique_players[existing_name]
                    unique_players[name] = data
                    fuzzy_duplicates_removed += 1
                elif data['confidence'] > existing_data['confidence']:
                    # Keep higher confidence
                    del unique_players[existing_name]
                    unique_players[name] = data
                    fuzzy_duplicates_removed += 1
                else:
                    fuzzy_duplicates_removed += 1

                found_duplicate = True
                break

        if not found_duplicate:
            # New unique player
            unique_players[name] = data

    player_data_list = list(unique_players.values())

    print(f"\nTotal unique players detected: {len(player_data_list)}")
    print(f"Sticky bottom cards removed: {sticky_cards_removed}")
    print(f"Fuzzy duplicates removed (name/rank/score matching): {fuzzy_duplicates_removed}")

    # Match and store scores
    print("\nMatching players and storing scores...")
    matched_count, unmatched, corrections_made = processor.match_and_store_scores(player_data_list, alliance_id)

    # Mark attendance
    if matched_count > 0:
        print(f"\nMarking attendance for {matched_count} matched players...")
        marked = processor.mark_attendance(event_name)
        print(f"Marked {marked} attendance records")

    # Generate report
    processor.generate_report(unmatched, len(image_files))

    print(f"\nAll data stored in db/web_cache.sqlite (Session ID: {session_id})")


if __name__ == "__main__":
    print("Enhanced OCR Attendance System with Score Tracking")
    print("="*60)

    # Process test screenshots
    process_event_screenshots(
        image_folder="test_screenshots",
        event_name="Bear Trap Test Event",
        event_type="Damage Rewards",
        alliance_id=None
    )
