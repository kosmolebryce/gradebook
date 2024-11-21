# tests/test_grade_calculation.py
import pytest
from gradebook.db import GradeBookError


def test_grade_calculation_empty_course(test_db, sample_course):
    """Test grade calculation for course with no assignments."""
    grade = test_db.calculate_course_grade(sample_course)
    assert grade == 0.0


def test_grade_calculation_single_category(test_db, sample_course):
    """Test grade calculation with assignments in one category."""
    # Get homework category
    cursor = test_db.cursor
    cursor.execute("""
        SELECT category_id FROM categories 
        WHERE course_id = ? AND category_name = 'Homework'
    """, (sample_course,))
    category_id = cursor.fetchone()[0]

    # Add some assignments
    test_db.add_assignment(sample_course, category_id, "HW1", 100, 90)
    test_db.add_assignment(sample_course, category_id, "HW2", 100, 80)

    grade = test_db.calculate_course_grade(sample_course)
    assert grade == pytest.approx(85.0, 0.1)


def test_grade_calculation_all_categories(test_db, sample_course):
    """Test grade calculation with assignments in all categories."""
    cursor = test_db.cursor

    # Add assignments to each category
    for category in ["Homework", "Midterm", "Final"]:
        cursor.execute("""
            SELECT category_id FROM categories 
            WHERE course_id = ? AND category_name = ?
        """, (sample_course, category))
        category_id = cursor.fetchone()[0]

        if category == "Homework":
            test_db.add_assignment(sample_course, category_id, "HW1", 100, 90)
            test_db.add_assignment(sample_course, category_id, "HW2", 100, 80)
        else:
            test_db.add_assignment(sample_course, category_id, f"{category} Exam", 100, 85)

    grade = test_db.calculate_course_grade(sample_course)
    assert grade == pytest.approx(85.0, 0.1)


def test_grade_calculation_weight_validation(test_db):
    """Test that grade calculation validates category weights."""
    course_id = test_db.add_course("TEST102", "Invalid Weights", "Fall 2024")

    # Test adding invalid weights
    with pytest.raises(GradeBookError) as exc_info:
        test_db.add_categories(course_id, [
            ("Cat1", 0.5),
            ("Cat2", 0.2)
        ])
    assert "Category weights must sum to 100%" in str(exc_info.value)

    # Test adding valid weights
    test_db.add_categories(course_id, [
        ("Cat1", 0.6),
        ("Cat2", 0.4)
    ])
    assert test_db.validate_category_weights(course_id)
