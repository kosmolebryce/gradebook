# migrations/002_add_credit_hours.py

import sqlite3
from datetime import datetime
from pathlib import Path
import shutil
from typing import Optional


def backup_database(db_path: Path) -> Optional[Path]:
    """Create a backup of the database before migration."""
    if not db_path.exists():
        return None

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = db_path.parent / f"{db_path.name}.{timestamp}.bak"
    shutil.copy2(db_path, backup_path)
    return backup_path


def migrate_database(db_path: Path) -> None:
    """Add credit_hours column to courses table."""
    try:
        # Backup the database
        backup_path = backup_database(db_path)
        if backup_path:
            print(f"Created backup at: {backup_path}")

        # Connect to database
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        try:
            # First try to remove the column if it exists (SQLite doesn't support ALTER TABLE DROP COLUMN)
            cursor.execute(
                "CREATE TABLE courses_new AS SELECT course_id, course_code, course_title, semester FROM courses")
            cursor.execute("DROP TABLE courses")
            cursor.execute("""
                CREATE TABLE courses (
                    course_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_code TEXT NOT NULL,
                    course_title TEXT NOT NULL,
                    semester TEXT NOT NULL,
                    credit_hours INTEGER NOT NULL DEFAULT 3 CHECK (credit_hours >= 0),
                    UNIQUE(course_code, semester)
                )
            """)
            cursor.execute(
                "INSERT INTO courses (course_id, course_code, course_title, semester, credit_hours) SELECT course_id, course_code, course_title, semester, 3 FROM courses_new")
            cursor.execute("DROP TABLE courses_new")

            conn.commit()
            print("\nMigration completed successfully!")
            print("Recreated courses table with credit_hours column (allows zero credits)")

        except sqlite3.OperationalError as e:
            print(f"Migration error: {e}")
            raise

    except Exception as e:
        print(f"Migration failed: {str(e)}")
        if backup_path:
            print(f"Restore from backup at: {backup_path}")
        raise
    finally:
        if 'conn' in locals():
            conn.close()


if __name__ == "__main__":
    db_path = Path("~/.gradebook/gradebook.db").expanduser()
    migrate_database(db_path)