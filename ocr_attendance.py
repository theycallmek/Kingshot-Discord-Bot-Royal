"""
OCR Attendance System
Processes event screenshots and marks player attendance in database
"""

import easyocr
import os
from pathlib import Path
import time
from datetime import datetime
from sqlmodel import Session, select, create_engine
from web.models import User, AttendanceRecord
import re

# Database Setup
DB_DIR = os.path.abspath("db")
os.makedirs(DB_DIR, exist_ok=True)
users_engine = create_engine(f"sqlite:///{os.path.join(DB_DIR, 'users.sqlite')}", connect_args={"check_same_thread": False})
attendance_engine = create_engine(f"sqlite:///{os.path.join(DB_DIR, 'attendance.sqlite')}", connect_args={"check_same_thread": False})


class OCRAttendanceProcessor:
    def __init__(self):
        """Initialize EasyOCR reader"""
        print("Initializing EasyOCR reader...")
        self.reader = easyocr.Reader(['en'], gpu=False, verbose=False)
        print("OCR reader ready!")

    def extract_player_names(self, image_path):
        """Extract player names from a screenshot"""
        print(f"\nProcessing: {os.path.basename(image_path)}")

        # Read text from image
        results = self.reader.readtext(image_path)

        # Filter for player names (starting with [DOA or containing DOA in brackets)
        player_names = []
        for (bbox, text, confidence) in results:
            # Look for text starting with [DOA
            if text.startswith('[DOA') and confidence > 0.6:  # 60% confidence threshold
                # Clean up the name
                cleaned_name = self.clean_player_name(text)
                if cleaned_name:
                    player_names.append({
                        'raw': text,
                        'cleaned': cleaned_name,
                        'confidence': confidence
                    })
                    print(f"  Found: {cleaned_name} (confidence: {confidence:.1%})")

        return player_names

    def clean_player_name(self, raw_name):
        """Clean up OCR errors in player names"""
        # Remove common OCR errors
        cleaned = raw_name.strip()

        # Fix common bracket OCR errors [DOAJ -> [DOA]
        cleaned = cleaned.replace('[DOAJ', '[DOA]')
        cleaned = cleaned.replace('[DOAj', '[DOA]')

        # Remove any trailing spaces or special characters
        cleaned = cleaned.strip()

        return cleaned

    def match_players_in_database(self, extracted_names, alliance_id=None):
        """Match extracted names against database users"""
        matched = []
        unmatched = []

        with Session(users_engine) as session:
            # Get all users (optionally filter by alliance)
            query = select(User)
            if alliance_id:
                query = query.where(User.alliance == alliance_id)

            all_users = session.exec(query).all()

            # Create a lookup dictionary
            users_by_nickname = {user.nickname.lower(): user for user in all_users}

            for name_data in extracted_names:
                cleaned_name = name_data['cleaned']

                # Try exact match (case insensitive)
                if cleaned_name.lower() in users_by_nickname:
                    user = users_by_nickname[cleaned_name.lower()]
                    matched.append({
                        'extracted': cleaned_name,
                        'user': user,
                        'confidence': name_data['confidence']
                    })
                else:
                    # Try fuzzy matching (remove brackets and alliance tags)
                    # Extract just the player name without [DOA] prefix
                    name_without_tag = re.sub(r'\[.*?\]', '', cleaned_name).strip()

                    # Try matching without the tag
                    found = False
                    for db_nickname, user in users_by_nickname.items():
                        db_name_without_tag = re.sub(r'\[.*?\]', '', db_nickname).strip()
                        if name_without_tag.lower() == db_name_without_tag.lower():
                            matched.append({
                                'extracted': cleaned_name,
                                'user': user,
                                'confidence': name_data['confidence']
                            })
                            found = True
                            break

                    if not found:
                        unmatched.append(name_data)

        return matched, unmatched

    def mark_attendance(self, matched_players, event_name, event_date=None):
        """Mark attendance for matched players in database"""
        if event_date is None:
            event_date = datetime.now()

        # Create a session_id based on event and date
        session_id = f"{event_name}_{event_date.strftime('%Y%m%d')}"

        marked_count = 0

        with Session(attendance_engine) as session:
            for match in matched_players:
                user = match['user']

                # Check if attendance already exists for this user/event/date
                existing = session.exec(
                    select(AttendanceRecord)
                    .where(AttendanceRecord.player_id == str(user.fid))
                    .where(AttendanceRecord.session_id == session_id)
                ).first()

                if existing:
                    print(f"  Attendance already recorded for {user.nickname}")
                else:
                    # Create new attendance record
                    attendance = AttendanceRecord(
                        session_id=session_id,
                        session_name=event_name,
                        event_type="OCR Import",
                        event_date=event_date,
                        player_id=str(user.fid),
                        player_name=user.nickname,
                        alliance_id=str(user.alliance),
                        alliance_name=f"Alliance {user.alliance}",  # You can update this with actual alliance names later
                        status="present",
                        points=0,
                        marked_at=datetime.now(),
                        marked_by="OCR_System",
                        marked_by_username="Automated OCR",
                        created_at=datetime.now()
                    )
                    session.add(attendance)
                    marked_count += 1
                    print(f"  Marked attendance for {user.nickname}")

            session.commit()

        return marked_count

    def generate_report(self, matched_players, unmatched_names, total_processed):
        """Generate attendance report"""
        print("\n" + "="*60)
        print("ATTENDANCE REPORT")
        print("="*60)

        print(f"\nImages Processed: {total_processed}")
        print(f"Total Players Found: {len(matched_players) + len(unmatched_names)}")
        print(f"Successfully Matched: {len(matched_players)}")
        print(f"Unable to Match: {len(unmatched_names)}")

        if matched_players:
            print("\n--- MATCHED PLAYERS ---")
            # Remove duplicates by FID
            unique_matched = {}
            for match in matched_players:
                fid = match['user'].fid
                if fid not in unique_matched:
                    unique_matched[fid] = match

            for match in unique_matched.values():
                user = match['user']
                print(f"  {user.nickname} (FID: {user.fid}, Alliance: {user.alliance}) - {match['confidence']:.1%}")

        if unmatched_names:
            print("\n--- UNMATCHED NAMES (Need Manual Review) ---")
            for name_data in unmatched_names:
                print(f"  {name_data['cleaned']} (confidence: {name_data['confidence']:.1%})")
                print(f"    Original OCR: {name_data['raw']}")

        print("\n" + "="*60)


def process_attendance_screenshots(image_folder, event_name, alliance_id=None):
    """
    Main function to process attendance screenshots

    Args:
        image_folder: Path to folder containing screenshots
        event_name: Name of the event (e.g., "Showdown", "Bear Trap")
        alliance_id: Optional alliance ID to filter users
    """
    processor = OCRAttendanceProcessor()

    # Get all images
    folder = Path(image_folder)
    image_files = list(folder.glob('*.png')) + list(folder.glob('*.jpg')) + list(folder.glob('*.jpeg'))

    if not image_files:
        print(f"No images found in {image_folder}")
        return

    print(f"\nFound {len(image_files)} image(s) to process")
    print(f"Event: {event_name}")

    # Process all images and collect unique players
    all_extracted = []
    for image_path in image_files:
        extracted = processor.extract_player_names(str(image_path))
        all_extracted.extend(extracted)

    # Remove duplicates based on cleaned name
    unique_extracted = {}
    for item in all_extracted:
        cleaned = item['cleaned']
        if cleaned not in unique_extracted:
            unique_extracted[cleaned] = item

    extracted_list = list(unique_extracted.values())

    print(f"\nTotal unique players extracted: {len(extracted_list)}")

    # Match against database
    print("\nMatching players against database...")
    matched, unmatched = processor.match_players_in_database(extracted_list, alliance_id)

    # Mark attendance
    if matched:
        print(f"\nMarking attendance for {len(matched)} matched players...")
        marked_count = processor.mark_attendance(matched, event_name)
        print(f"Marked {marked_count} new attendance records")

    # Generate report
    processor.generate_report(matched, unmatched, len(image_files))


if __name__ == "__main__":
    # Example usage
    print("OCR Attendance System")
    print("="*60)

    # Process test screenshots
    process_attendance_screenshots(
        image_folder="test_screenshots",
        event_name="Bear Trap - Test Event",
        alliance_id=None  # Set to specific alliance ID to filter, or None for all
    )
