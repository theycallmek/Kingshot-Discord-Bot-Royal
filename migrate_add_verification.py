"""
Migration script to add verification tracking columns to ocr_event_data table
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join("db", "web_cache.sqlite")


def add_verification_columns():
    """Add verification tracking columns to ocr_event_data table"""

    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        return False

    # Backup first
    backup_path = DB_PATH.replace('.sqlite', f'_backup_verification_{datetime.now().strftime("%Y%m%d_%H%M%S")}.sqlite')
    import shutil
    shutil.copy2(DB_PATH, backup_path)
    print(f"[OK] Backup created: {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # Check if columns already exist
        cursor.execute("PRAGMA table_info(ocr_event_data)")
        columns = [row[1] for row in cursor.fetchall()]

        columns_to_add = []
        if 'verification_count' not in columns:
            columns_to_add.append(('verification_count', 'INTEGER DEFAULT 1'))
        if 'verified_sessions' not in columns:
            columns_to_add.append(('verified_sessions', 'TEXT'))
        if 'data_confidence' not in columns:
            columns_to_add.append(('data_confidence', 'REAL DEFAULT 1.0'))

        if not columns_to_add:
            print("[OK] Verification columns already exist")
            conn.close()
            return True

        # Add new columns
        print(f"\nAdding {len(columns_to_add)} new columns...")
        for col_name, col_type in columns_to_add:
            sql = f"ALTER TABLE ocr_event_data ADD COLUMN {col_name} {col_type}"
            cursor.execute(sql)
            print(f"  [OK] Added column: {col_name}")

        # Initialize existing records
        print("\nInitializing existing records...")
        cursor.execute("""
            UPDATE ocr_event_data
            SET verification_count = 1,
                verified_sessions = processing_session,
                data_confidence = 1.0
            WHERE verification_count IS NULL
        """)
        updated = cursor.rowcount
        print(f"  [OK] Initialized {updated} existing records")

        conn.commit()
        print("\n[OK] Migration completed successfully!")
        return True

    except Exception as e:
        print(f"[ERROR] Migration failed: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    print("="*70)
    print("OCR Verification Columns Migration")
    print("="*70)
    print("\nThis will add verification tracking to ocr_event_data:")
    print("  - verification_count: Number of sessions that verified the data")
    print("  - verified_sessions: List of session IDs that verified")
    print("  - data_confidence: Confidence multiplier (1.0-2.0)")

    response = input("\nProceed with migration? (yes/no): ")
    if response.lower() in ['yes', 'y']:
        add_verification_columns()
    else:
        print("Migration cancelled")
