# tests/test_category_management.py
import pytest
from gradebook.db import GradeBookError


def test_category_weight_validation(test_db):
    """Test category weight validation."""
    course_id = test_db.add_course("TEST201", "Weight Test", "Fall 2024")

    # Test adding categories that sum to 100%
    categories = [
        ("Exams", 0.6),
        ("Homework", 0.4)
    ]
    test_db.add_categories(course_id, categories)

    # Verify weights
    cursor = test_db.cursor
    cursor.execute("""
        SELECT SUM(weight) FROM categories WHERE course_id = ?
    """, (course_id,))
    total_weight = cursor.fetchone()[0]
    assert abs(total_weight - 1.0) <= 0.0001


def test_category_weight_update(test_db):
    """Test updating category weights."""
    course_id = test_db.add_course("TEST202", "Update Test", "Fall 2024")

    # Add initial categories
    test_db.add_category(course_id, "Exams", 0.7)
    test_db.add_category(course_id, "Homework", 0.3)

    # Get category ID for Exams
    cursor = test_db.cursor
    cursor.execute("""
        SELECT category_id FROM categories 
        WHERE course_id = ? AND category_name = 'Exams'
    """, (course_id,))
    category_id = cursor.fetchone()[0]

    # Update weight - should create Unallocated category
    test_db.update_category_weight(category_id, 0.6)

    # Verify weights
    cursor.execute("""
        SELECT category_name, weight 
        FROM categories 
        WHERE course_id = ?
        ORDER BY category_name
    """, (course_id,))

    categories = cursor.fetchall()
    weights = {name: weight for name, weight in categories}

    assert abs(weights['Exams'] - 0.6) <= 0.0001
    assert abs(weights['Homework'] - 0.3) <= 0.0001
    assert abs(weights['Unallocated'] - 0.1) <= 0.0001


def test_category_assignment_preservation(test_db):
    """Test that assignments are preserved when modifying categories."""
    course_id = test_db.add_course("TEST203", "Preservation Test", "Fall 2024")

    # Add initial category
    test_db.add_category(course_id, "Homework", 1.0)

    # Get category ID
    cursor = test_db.cursor
    cursor.execute("""
        SELECT category_id FROM categories 
        WHERE course_id = ? AND category_name = 'Homework'
    """, (course_id,))
    category_id = cursor.fetchone()[0]

    # Add assignments
    test_db.add_assignment(course_id, category_id, "HW1", 100, 90)
    test_db.add_assignment(course_id, category_id, "HW2", 100, 85)

    # Update category structure
    new_categories = [
        ("Assignments", 0.5),
        ("Exams", 0.5)
    ]

    # This should preserve existing assignments
    test_db.add_categories(course_id, new_categories)

    # Verify assignments still exist
    cursor.execute("""
        SELECT COUNT(*) FROM assignments WHERE course_id = ?
    """, (course_id,))

    assert cursor.fetchone()[0] == 2

def test_category_weight_range_validation(test_db):
    """Test that category weights must be between 0 and 1."""
    course_id = test_db.add_course("TEST101", "Test Course", "Fall 2024")
    with pytest.raises(GradeBookError) as exc_info:
        test_db.add_category(course_id, "Invalid", 50)
    assert "Weight must be between 0 and 1" in str(exc_info.value)