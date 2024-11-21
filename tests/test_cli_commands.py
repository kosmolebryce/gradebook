# tests/test_cli_commands.py
from click.testing import CliRunner
from gradebook.cli import cli, GradeBookCLI
from typing import Sequence

from gradebook.db import Gradebook


def test_view_courses_command(runner: CliRunner, test_db: Gradebook, test_db_path: str):
    """Test the view courses command."""

    # Create test data
    test_db.add_course("TEST301", "Test Course 1", "Fall 2024")
    test_db.add_course("TEST302", "Test Course 2", "Fall 2024")
    test_db.conn.commit()

    # Verify entry in database
    courses = test_db.get_all_courses()
    assert len(courses) >= 2, "Failed to add test courses to the database"

    result = runner.invoke(cli, ['--db-path', str(test_db_path), 'view', 'courses'])
    assert result.exit_code == 0
    assert "TEST301" in result.output
    assert "TEST302" in result.output


def test_view_course_details(runner: CliRunner, test_db: Gradebook, test_db_path: str):
    """Test viewing detailed course information."""
    course_id = test_db.add_course("TEST303", "Detailed Test", "Fall 2024")
    # Add categories to sum to 100% weight
    test_db.add_category(course_id, "Assignments", 0.5)  # Changed from 50 to 0.5
    test_db.add_category(course_id, "Exams", 0.5)       # Changed from 50 to 0.5
    test_db.conn.commit()

    # Verify entry in database
    course_details = test_db.get_course_summary(course_id)
    assert course_details is not None, "Failed to retrieve course details from the database"

    result = runner.invoke(cli, ['--db-path', str(test_db_path), 'view', 'course', 'TEST303'])
    assert result.exit_code == 0
    assert "Detailed Test" in result.output