# migrations/validate_migration.py

import sys
from pathlib import Path
from gradebook.db import Gradebook


def validate_category_weights(gradebook: Gradebook) -> bool:
    """Verify all courses have properly normalized weights."""
    cursor = gradebook.cursor

    cursor.execute("SELECT course_id, course_code FROM courses")
    courses = cursor.fetchall()

    all_valid = True
    for course_id, course_code in courses:
        # Get total weights
        cursor.execute("""
            SELECT SUM(weight) 
            FROM categories 
            WHERE course_id = ?
        """, (course_id,))

        total_weight = cursor.fetchone()[0] or 0

        if abs(total_weight - 1.0) > 0.0001:
            print(f"[ERROR] Invalid weights for {course_code}: {total_weight:.4f}")
            all_valid = False

            # Show category breakdown
            cursor.execute("""
                SELECT category_name, weight
                FROM categories
                WHERE course_id = ?
                ORDER BY weight DESC
            """, (course_id,))

            for name, weight in cursor.fetchall():
                print(f"  - {name}: {weight:.4f}")

    return all_valid


def validate_grade_calculations(gradebook: Gradebook) -> bool:
    """Verify grade calculations are working correctly."""
    cursor = gradebook.cursor

    cursor.execute("""
        SELECT c.course_id, c.course_code,
               COUNT(DISTINCT a.assignment_id) as assignment_count
        FROM courses c
        LEFT JOIN assignments a ON c.course_id = a.course_id
        GROUP BY c.course_id
        HAVING assignment_count > 0
    """)

    courses = cursor.fetchall()
    all_valid = True

    for course_id, course_code, _ in courses:
        try:
            grade = gradebook.calculate_course_grade(course_id)
            print(f"Successfully calculated grade for {course_code}: {grade:.2f}%")

            # Verify category-level calculations
            cursor.execute("""
                SELECT c.category_name, c.weight,
                       COUNT(a.assignment_id) as assignment_count
                FROM categories c
                LEFT JOIN assignments a ON c.category_id = a.category_id
                WHERE c.course_id = ?
                GROUP BY c.category_id
            """, (course_id,))

            for cat_name, weight, count in cursor.fetchall():
                if count > 0:
                    print(f"  - {cat_name}: {count} assignments, weight={weight:.2f}")

        except Exception as e:
            print(f"[ERROR] Failed to calculate grade for {course_code}: {str(e)}")
            all_valid = False

    return all_valid


def validate_database(db_path: Path) -> bool:
    """Run all validation checks."""
    try:
        print(f"Validating database at: {db_path}")
        print("-" * 50)

        gradebook = Gradebook(db_path)

        # Check category weights
        print("\nValidating category weights...")
        weights_valid = validate_category_weights(gradebook)

        # Check grade calculations
        print("\nValidating grade calculations...")
        grades_valid = validate_grade_calculations(gradebook)

        # Overall status
        print("\nValidation Summary")
        print("-" * 50)
        print(f"Category Weights: {'✓' if weights_valid else '✗'}")
        print(f"Grade Calculations: {'✓' if grades_valid else '✗'}")

        return weights_valid and grades_valid

    except Exception as e:
        print(f"Validation failed: {str(e)}")
        return False
    finally:
        if 'gradebook' in locals():
            gradebook.close()


if __name__ == "__main__":
    db_path = Path("~/.gradebook/gradebook.db").expanduser()
    success = validate_database(db_path)
    sys.exit(0 if success else 1)