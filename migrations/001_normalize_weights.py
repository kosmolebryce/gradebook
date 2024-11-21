# migrations/001_normalize_weights.py

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional


def backup_database(db_path: Path) -> Optional[Path]:
    """Create a backup of the database before migration."""
    if not db_path.exists():
        return None

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = db_path.parent / f"{db_path.name}.{timestamp}.bak"
    shutil.copy2(db_path, backup_path)
    return backup_path


def migrate_course_weights(cursor: sqlite3.Cursor, course_id: int) -> None:
    """Normalize weights for a single course."""
    # Get all categories except Unallocated
    cursor.execute("""
        SELECT category_id, weight
        FROM categories
        WHERE course_id = ? AND LOWER(category_name) != 'unallocated'
    """, (course_id,))

    categories = cursor.fetchall()
    total_weight = sum(weight for _, weight in categories)

    if abs(total_weight - 1.0) > 0.0001:  # Needs normalization
        # Check for Unallocated category
        cursor.execute("""
            SELECT category_id, weight
            FROM categories
            WHERE course_id = ? AND LOWER(category_name) = 'unallocated'
        """, (course_id,))

        unallocated = cursor.fetchone()

        if total_weight < 1.0:  # Need to create/update Unallocated
            remaining = 1.0 - total_weight
            if unallocated:
                cursor.execute("""
                    UPDATE categories
                    SET weight = ?
                    WHERE category_id = ?
                """, (remaining, unallocated[0]))
            else:
                cursor.execute("""
                    INSERT INTO categories (course_id, category_name, weight)
                    VALUES (?, 'Unallocated', ?)
                """, (course_id, remaining))
        else:  # Need to normalize existing weights
            scale_factor = 1.0 / total_weight
            for cat_id, weight in categories:
                new_weight = weight * scale_factor
                cursor.execute("""
                    UPDATE categories
                    SET weight = ?
                    WHERE category_id = ?
                """, (new_weight, cat_id))

            if unallocated:  # Remove any Unallocated category
                cursor.execute("""
                    DELETE FROM categories
                    WHERE category_id = ?
                """, (unallocated[0],))


def migrate_database(db_path: Path) -> None:
    """Main migration function."""
    try:
        # Backup the database
        backup_path = backup_database(db_path)
        if backup_path:
            print(f"Created backup at: {backup_path}")

        # Connect to database
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get all courses
        cursor.execute("SELECT course_id FROM courses")
        courses = cursor.fetchall()

        for (course_id,) in courses:
            try:
                migrate_course_weights(cursor, course_id)
                conn.commit()
                print(f"Migrated weights for course ID: {course_id}")
            except Exception as e:
                conn.rollback()
                print(f"Error migrating course {course_id}: {str(e)}")
                raise

        print("\nMigration completed successfully!")

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