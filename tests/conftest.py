# tests/conftest.py
import pytest
import tempfile
from click.testing import CliRunner
from pathlib import Path
from gradebook.db import Gradebook

@pytest.fixture(scope="session")
def runner():
    """Create a Click CLI runner."""
    return CliRunner()

@pytest.fixture
def test_db_path(tmp_path):
    """Create a persistent test database path."""
    return tmp_path / "test.db"

@pytest.fixture
def test_db(test_db_path):
    """Create a test database with a persistent path."""
    db = Gradebook(test_db_path)
    db.create_tables()
    assert db is not None, "Failed to instantiate the Gradebook database."
    yield db
    db.close()

@pytest.fixture
def sample_course(test_db):
    """Create a sample course with categories."""
    course_id = test_db.add_course("TEST101", "Test Course", "Fall 2024")

    categories = [
        ("Homework", 0.3),
        ("Midterm", 0.3),
        ("Final", 0.4)
    ]

    test_db.add_categories(course_id, categories)
    return course_id