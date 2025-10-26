"""
Migration script to clean up web_cache.sqlite database
Removes duplicate tables and migrates to streamlined OCR schema
"""

import sqlite3
import os
from datetime import datetime
from pathlib import Path

DB_PATH = os.path.join("db", "web_cache.sqlite")

# Tables that should NOT be in web_cache.sqlite (duplicates from other DBs)
TABLES_TO_DROP = [
    "gift_codes",           # Should only be in giftcode.sqlite
    "users",                # Should only be in users.sqlite
    "nickname_changes",     # Should only be in users.sqlite or changes.sqlite
    "furnace_changes",      # Should only be in changes.sqlite
    "attendance_records",   # Should only be in attendance.sqlite
    "bear_notifications",   # Can be removed if not needed for caching
    "alliance_list",        # Can be derived from users.sqlite
    "user_giftcodes",       # Should only be in giftcode.sqlite
    "bear_notification_embeds",  # Can be removed if not needed
    "notification_days",    # Can be removed if not needed
]

# Old OCR tables to migrate/drop
OLD_OCR_TABLES = [
    "ocr_event_sessions",
    "ocr_player_scores",
    "ocr_raw_data",
    "ocr_processing_logs",
    "ghost_players",
]


def backup_database():
    """Create a backup of the database before migration"""
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        return False

    backup_path = DB_PATH.replace('.sqlite', f'_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.sqlite')

    import shutil
    shutil.copy2(DB_PATH, backup_path)
    print(f"[OK] Backup created: {backup_path}")
    return True


def migrate_old_data(conn):
    """Migrate useful data from old tables to new schema"""
    cursor = conn.cursor()

    print("\nMigrating data from old OCR tables...")

    # Check if old tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ocr_player_scores'")
    if not cursor.fetchone():
        print("  No old OCR data to migrate")
        return

    # Migrate player mappings from ocr_player_scores
    print("  Migrating player mappings...")
    cursor.execute("""
        SELECT
            player_name,
            player_fid,
            confidence,
            MIN(extracted_at) as first_seen,
            MAX(extracted_at) as last_seen,
            COUNT(*) as times_seen
        FROM ocr_player_scores
        WHERE matched = 1 AND player_fid IS NOT NULL
        GROUP BY player_name, player_fid
    """)

    mappings = cursor.fetchall()
    migrated_mappings = 0

    for row in mappings:
        player_name, player_fid, confidence, first_seen, last_seen, times_seen = row
        cursor.execute("""
            INSERT OR IGNORE INTO ocr_player_mapping
            (player_name, player_fid, confidence, first_seen, last_seen, times_seen, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (player_name, player_fid, confidence, first_seen, last_seen, times_seen,
              datetime.now().isoformat(), datetime.now().isoformat()))
        migrated_mappings += 1

    # Migrate event data from ocr_player_scores + ocr_event_sessions
    print("  Migrating event data...")
    cursor.execute("""
        SELECT
            s.event_name,
            s.event_type,
            s.event_date,
            p.player_name,
            p.player_fid,
            p.ranking,
            p.damage_points,
            p.score_value,
            p.confidence,
            p.image_source,
            p.session_id,
            p.extracted_at
        FROM ocr_player_scores p
        JOIN ocr_event_sessions s ON p.session_id = s.session_id
    """)

    event_data = cursor.fetchall()
    migrated_events = 0

    for row in event_data:
        (event_name, event_type, event_date, player_name, player_fid, ranking,
         damage_points, score_value, confidence, image_source, session_id, extracted_at) = row

        cursor.execute("""
            INSERT INTO ocr_event_data
            (event_name, event_type, event_date, player_name, player_fid, ranking,
             rank_inferred, score, damage_points, time_value, ocr_confidence,
             image_source, processing_session, extracted_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (event_name, event_type, event_date, player_name, player_fid, ranking,
              False, score_value, damage_points, None, confidence, image_source,
              str(session_id), extracted_at, datetime.now().isoformat()))
        migrated_events += 1

    conn.commit()
    print(f"  [OK] Migrated {migrated_mappings} player mappings")
    print(f"  [OK] Migrated {migrated_events} event records")


def clean_database():
    """Remove duplicate and old tables from web_cache.sqlite"""
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        return

    # Create backup first
    if not backup_database():
        return

    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get list of existing tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = [row[0] for row in cursor.fetchall()]
    print(f"\nExisting tables: {len(existing_tables)}")

    # Create new OCR tables first (if they don't exist)
    print("\nCreating new OCR tables...")
    from sqlmodel import SQLModel, create_engine
    from web.ocr_models import OCRPlayerMapping, OCREventData

    cache_engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(cache_engine)
    print("  [OK] New OCR tables created")

    # Migrate data from old tables
    migrate_old_data(conn)

    # Drop duplicate tables
    print("\nRemoving duplicate tables...")
    dropped_count = 0
    for table in TABLES_TO_DROP:
        if table in existing_tables:
            try:
                cursor.execute(f"DROP TABLE IF EXISTS {table}")
                print(f"  [OK] Dropped: {table}")
                dropped_count += 1
            except Exception as e:
                print(f"  [FAIL] Failed to drop {table}: {e}")

    # Drop old OCR tables
    print("\nRemoving old OCR tables...")
    for table in OLD_OCR_TABLES:
        if table in existing_tables:
            try:
                cursor.execute(f"DROP TABLE IF EXISTS {table}")
                print(f"  [OK] Dropped: {table}")
                dropped_count += 1
            except Exception as e:
                print(f"  [FAIL] Failed to drop {table}: {e}")

    conn.commit()

    # Show final table list
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    final_tables = [row[0] for row in cursor.fetchall()]

    print("\n" + "="*60)
    print("MIGRATION COMPLETE")
    print("="*60)
    print(f"Tables removed: {dropped_count}")
    print(f"Remaining tables: {len(final_tables)}")
    print("\nFinal table list:")
    for table in sorted(final_tables):
        print(f"  - {table}")

    conn.close()


if __name__ == "__main__":
    print("="*60)
    print("OCR Cache Database Migration")
    print("="*60)
    print("\nThis script will:")
    print("  1. Create a backup of web_cache.sqlite")
    print("  2. Migrate data to new streamlined schema")
    print("  3. Remove duplicate tables from other databases")
    print("  4. Remove old OCR tables")

    response = input("\nProceed with migration? (yes/no): ")
    if response.lower() in ['yes', 'y']:
        clean_database()
    else:
        print("Migration cancelled")
