# gradebook/db.py

import sqlite3

from datetime import datetime
from pathlib import Path
from typing import List, Tuple


class GradeBookError(Exception):
    """Custom exception for Gradebook errors"""
    pass


class Gradebook:
    def __init__(self, db_path):
        if db_path is None:
            db_path = Path("~/.gradebook/gradebook.db").expanduser()

        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.cursor = self.conn.cursor()
        self.cursor.execute("PRAGMA foreign_keys = ON")

        # Always ensure database is properly initialized
        self.ensure_database_initialized()

    def verify_database_initialized(self) -> bool:
        """Verify that all required tables exist and have correct schema."""
        try:
            tables = ["courses", "categories", "assignments"]

            for table in tables:
                self.cursor.execute(f"""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name=?
                """, (table,))

                if not self.cursor.fetchone():
                    return False

            return True

        except GradeBookError:
            return False

    def ensure_database_initialized(self) -> None:
        """Ensure database is initialized, creating tables if needed."""
        if not self.verify_database_initialized():
            self.create_tables()

    # In db.py, update create_tables():

    def create_tables(self):
        self.cursor.executescript('''
            DROP TABLE IF EXISTS assignments;
            DROP TABLE IF EXISTS categories;
            DROP TABLE IF EXISTS courses;

            CREATE TABLE courses (
                course_id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_code TEXT NOT NULL,
                course_title TEXT NOT NULL,
                semester TEXT NOT NULL,
                credit_hours INTEGER NOT NULL DEFAULT 3 CHECK (credit_hours >= 0),
                UNIQUE(course_code, semester)
            );

            -- Rest of the schema remains the same...
            CREATE TABLE categories (
                category_id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER,
                category_name TEXT NOT NULL,
                weight REAL NOT NULL CHECK (weight >= 0 AND weight <= 1),
                FOREIGN KEY (course_id) REFERENCES courses(course_id) 
                    ON DELETE CASCADE
                    ON UPDATE CASCADE,
                UNIQUE(course_id, category_name)
            );

            CREATE TABLE assignments (
                assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER,
                category_id INTEGER,
                title TEXT NOT NULL,
                max_points REAL NOT NULL CHECK (max_points > 0),
                earned_points REAL NOT NULL CHECK (earned_points >= 0),
                entry_date TEXT NOT NULL,
                CHECK (earned_points <= max_points),
                FOREIGN KEY (course_id) REFERENCES courses(course_id) 
                    ON DELETE CASCADE
                    ON UPDATE CASCADE,
                FOREIGN KEY (category_id) REFERENCES categories(category_id)
                    ON DELETE CASCADE
                    ON UPDATE CASCADE
            );
        ''')
        self.conn.commit()

    def add_course(self, course_code: str, course_title: str, semester: str, credit_hours: int = 3) -> int:
        """Add a new course to the database."""
        try:
            self.cursor.execute('''
            INSERT INTO courses (course_code, course_title, semester, credit_hours)
            VALUES (?, ?, ?, ?)
            ''', (course_code.upper(), course_title, semester, credit_hours))
            self.conn.commit()
            return self.cursor.lastrowid
        except sqlite3.IntegrityError:
            raise GradeBookError(f"Course {course_code} already exists for {semester}")

    def update_course(self, course_id: int, course_code: str = None,
                      course_title: str = None, semester: str = None, credit_hours: int = None):
        """Update course details."""
        updates = []
        params = []
        if course_code:
            updates.append("course_code = ?")
            params.append(course_code.upper())
        if course_title:
            updates.append("course_title = ?")
            params.append(course_title)
        if semester:
            updates.append("semester = ?")
            params.append(semester)
        if credit_hours is not None:
            if credit_hours < 0:
                raise GradeBookError("Credit hours cannot be negative")
            updates.append("credit_hours = ?")
            params.append(credit_hours)

        if updates:
            query = f"UPDATE courses SET {', '.join(updates)} WHERE course_id = ?"
            params.append(course_id)
            self.cursor.execute(query, tuple(params))
            self.conn.commit()

    def get_all_courses(self) -> List[dict]:
        """Get a list of all courses."""
        self.cursor.execute('''
        SELECT course_id, course_code, course_title, semester, credit_hours 
        FROM courses 
        ORDER BY semester, course_code
        ''')
        rows = self.cursor.fetchall()

        return [
            {
                "course_id": row[0],
                "course_code": row[1],
                "course_title": row[2],
                "semester": row[3],
                "credit_hours": row[4]
            }
            for row in rows
        ]

    def validate_category_weights(self, course_id: int, new_category_weight: float = 0) -> bool:
        """
        Validate that all category weights for a course sum to 1.0 (100%).
        """
        self.cursor.execute('''
        SELECT SUM(weight) FROM categories WHERE course_id = ?
        ''', (course_id,))
        current_sum = self.cursor.fetchone()[0] or 0
        total_sum = current_sum + new_category_weight

        # Allow sums that are exactly 1.0 or very close to it
        tolerance = 0.0001
        return abs(total_sum - 1.0) <= tolerance

    def get_remaining_weight(self, course_id: int) -> float:
        """Get remaining unallocated weight for a course."""
        self.cursor.execute('''
            SELECT IFNULL(SUM(weight), 0) FROM categories WHERE course_id=?
        ''', (course_id,))
        used_weight = self.cursor.fetchone()[0]
        # Return actual decimal instead of percentage
        return 1.0 - used_weight  # Changed from 100.0 to 1.0

    def add_category(self, course_id: int, category_name: str, weight: float) -> int:
        """Add a new category for a course."""
        if not (0 <= weight <= 1):
            raise GradeBookError(
                f"Weight must be between 0 and 1 (0% to 100%). Got: {weight * 100}%"
            )

        # First check for Unallocated category specifically
        self.cursor.execute("""
            SELECT category_id, weight 
            FROM categories 
            WHERE course_id = ? AND LOWER(category_name) = 'unallocated'
        """, (course_id,))
        
        unallocated = self.cursor.fetchone()
        if not unallocated:
            raise GradeBookError("No Unallocated weight available")

        unallocated_id, unallocated_weight = unallocated
        tolerance = 0.0001

        # Check if weights match within tolerance
        if abs(weight - unallocated_weight) <= tolerance:
            # Weights match exactly (within tolerance) - delete Unallocated and add new category
            self.cursor.execute("DELETE FROM categories WHERE category_id = ?", (unallocated_id,))
        elif weight > unallocated_weight:
            raise GradeBookError(
                f"Invalid weight {weight * 100}%. Available in Unallocated: {unallocated_weight * 100}%"
            )
        else:
            # Update Unallocated with remaining weight
            new_unallocated = unallocated_weight - weight
            self.cursor.execute(
                "UPDATE categories SET weight = ? WHERE category_id = ?", 
                (new_unallocated, unallocated_id)
            )

        # Add the new category
        try:
            self.cursor.execute('''
            INSERT INTO categories (course_id, category_name, weight)
            VALUES (?, ?, ?)
            ''', (course_id, category_name, weight))
            self.conn.commit()
            return self.cursor.lastrowid
        except sqlite3.IntegrityError:
            self.conn.rollback()
            raise GradeBookError(f"Category '{category_name}' already exists for this course")

        def add_categories(self, course_id: int, categories: List[Tuple[str, float]]):
            """Add multiple categories for a course at once."""
            total_weight = sum(weight for _, weight in categories)
            tolerance = 0.0001

            if not abs(total_weight - 1.0) <= tolerance:  # Allow exact 100% with tolerance
                raise GradeBookError(
                    f"Category weights must sum to 100% (got {total_weight * 100:.2f}%, tolerance: ±{tolerance * 100:.2f}%)"
                )

            try:
                for category_name, weight in categories:
                    self.cursor.execute('''
                    INSERT INTO categories (course_id, category_name, weight)
                    VALUES (?, ?, ?)
                    ''', (course_id, category_name, weight))
                self.conn.commit()
            except sqlite3.IntegrityError:
                self.conn.rollback()
                raise GradeBookError("Duplicate category names are not allowed")

    def ensure_unassigned_category(self, course_id: int) -> int:
        """
        Ensure an 'Unassigned' category exists for the course and return its ID.
        Creates with zero weight if it doesn't exist.
        """
        self.cursor.execute('''
        SELECT category_id FROM categories 
        WHERE course_id = ? AND category_name = 'Unassigned'
        ''', (course_id,))
        result = self.cursor.fetchone()

        if result:
            return result[0]

        # Create new Unassigned category with 0 weight
        self.cursor.execute('''
        INSERT INTO categories (course_id, category_name, weight)
        VALUES (?, 'Unassigned', 0.0)
        ''', (course_id,))
        self.conn.commit()
        return self.cursor.lastrowid

    def update_category_weight(self, category_id: int, new_weight: float):
        """Update the weight of a category."""
        # Get course_id for the category
        self.cursor.execute('''
        SELECT course_id, weight FROM categories WHERE category_id = ?
        ''', (category_id,))
        result = self.cursor.fetchone()
        if not result:
            raise GradeBookError("Category not found")

        course_id, current_weight = result
        weight_difference = current_weight - new_weight

        if weight_difference > 0:  # Reducing weight, need to create/update Unallocated
            # Check for existing Unallocated category
            self.cursor.execute('''
            SELECT category_id, weight 
            FROM categories 
            WHERE course_id = ? AND LOWER(category_name) = 'unallocated'
            ''', (course_id,))
            unallocated = self.cursor.fetchone()

            if unallocated:
                # Update existing Unallocated category
                new_unallocated_weight = unallocated[1] + weight_difference
                self.cursor.execute('''
                UPDATE categories SET weight = ? WHERE category_id = ?
                ''', (new_unallocated_weight, unallocated[0]))
            else:
                # Create new Unallocated category
                self.cursor.execute('''
                INSERT INTO categories (course_id, category_name, weight)
                VALUES (?, 'Unallocated', ?)
                ''', (course_id, weight_difference))

        # Update the category weight
        self.cursor.execute('''
        UPDATE categories SET weight = ? WHERE category_id = ?
        ''', (new_weight, category_id))

        self.conn.commit()

    def update_category(self, category_id: int, category_name: str = None, weight: float = None):
        """Update category details."""
        updates = []
        params = []

        if category_name:
            updates.append("category_name = ?")
            params.append(category_name)
        if weight is not None:
            # Fetch course_id to validate weight constraints
            self.cursor.execute('SELECT course_id FROM categories WHERE category_id = ?', (category_id,))
            course_id = self.cursor.fetchone()
            if not course_id:
                raise GradeBookError("Category not found")

            course_id = course_id[0]
            remaining_weight = self.get_remaining_weight(course_id)
            self.cursor.execute('SELECT weight FROM categories WHERE category_id = ?', (category_id,))
            current_weight = self.cursor.fetchone()[0]

            # Allow weight adjustments within remaining limits
            if weight > (remaining_weight + current_weight):
                raise GradeBookError(f"New weight {weight} exceeds available limit")

            updates.append("weight = ?")
            params.append(weight)

        if updates:
            query = f"UPDATE categories SET {', '.join(updates)} WHERE category_id = ?"
            params.append(category_id)
            self.cursor.execute(query, tuple(params))
            self.conn.commit()

    def remove_category(self, category_id: int, preserve_assignments: bool = True) -> tuple[str, int]:
        """
        Remove a category, optionally preserving its assignments.

        Args:
            category_id: The ID of the category to remove
            preserve_assignments: If True, move assignments to Unassigned category
                                If False, let cascade delete handle assignments

        Returns:
            Tuple of (category_name, number_of_assignments_affected)
        """
        # Get category details before deletion
        self.cursor.execute('''
            SELECT category_name, course_id,
                   (SELECT COUNT(*) FROM assignments WHERE category_id = c.category_id) as assignment_count
            FROM categories c 
            WHERE category_id = ?
        ''', (category_id,))

        result = self.cursor.fetchone()
        if not result:
            raise GradeBookError("Category not found")

        category_name, course_id, assignment_count = result

        if category_name.lower() == 'unassigned':
            raise GradeBookError("Cannot remove the Unassigned category")

        if preserve_assignments and assignment_count > 0:
            # Get/create Unassigned category
            unassigned_id = self.ensure_unassigned_category(course_id)

            # Move assignments
            self.cursor.execute('''
                UPDATE assignments 
                SET category_id = ? 
                WHERE category_id = ?
            ''', (unassigned_id, category_id))

        # Remove category (cascade will handle assignment deletion if not preserved)
        self.cursor.execute('DELETE FROM categories WHERE category_id = ?', (category_id,))
        self.conn.commit()

        return category_name, assignment_count

    def remove_course(self, course_id: int) -> tuple[str, str, str, int]:
        """Remove a course and all its associated data."""
        self.cursor.execute('''
            SELECT course_code, course_title, semester,
                   (SELECT COUNT(*) FROM assignments WHERE course_id = c.course_id) as assignment_count
            FROM courses c
            WHERE course_id = ?
        ''', (course_id,))

        result = self.cursor.fetchone()
        if not result:
            raise GradeBookError("Course not found")

        course_code, course_title, semester, assignment_count = result

        # The ON DELETE CASCADE in the schema will handle the deletions
        self.cursor.execute('DELETE FROM courses WHERE course_id = ?', (course_id,))
        self.conn.commit()

        return course_code, course_title, semester, assignment_count

    def add_assignment(self, course_id: int, category_id: int, title: str,
                       max_points: float, earned_points: float) -> int:
        """Add a new assignment."""
        # Verify category belongs to course
        self.cursor.execute('''
        SELECT COUNT(*) FROM categories 
        WHERE category_id = ? AND course_id = ?
        ''', (category_id, course_id))
        if self.cursor.fetchone()[0] == 0:
            raise GradeBookError("Category does not belong to this course")

        # Check for duplicate assignment
        self.cursor.execute('''
        SELECT COUNT(*) FROM assignments
        WHERE course_id = ? AND title = ?
        ''', (course_id, title))
        if self.cursor.fetchone()[0] > 0:
            raise GradeBookError(f"Assignment '{title}' already exists in this course")

        entry_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.cursor.execute('''
        INSERT INTO assignments (course_id, category_id, title, max_points, earned_points, entry_date)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (course_id, category_id, title, max_points, earned_points, entry_date))
        self.conn.commit()
        return self.cursor.lastrowid

    def calculate_course_grade(self, course_id: int) -> float:
        """Calculate the overall weighted grade for a course, handling extra credit properly."""
        if not self.validate_category_weights(course_id):
            raise GradeBookError("Category weights do not sum to 100%")

        # Get all categories except Unallocated
        self.cursor.execute('''
            SELECT category_id, category_name, weight
            FROM categories 
            WHERE course_id = ? AND LOWER(category_name) != 'unallocated'
            ORDER BY category_name
        ''', (course_id,))

        categories = self.cursor.fetchall()
        if not categories:
            return 0.0

        total_weighted_grade = 0.0
        total_weight = 0.0

        for cat_id, cat_name, weight in categories:
            earned, possible = self.get_category_grade(cat_id)

            if possible > 0:  # Only include categories with assignments
                # Allow grade to exceed 100% for categories with extra credit
                grade = (earned / possible) * 100
                total_weighted_grade += grade * weight
                total_weight += weight

        if total_weight == 0:
            return 0.0

        # Normalize by actual weight used
        final_grade = total_weighted_grade / total_weight
        return round(final_grade, 2)

    def get_grade_breakdown(self, course_id: int) -> dict:
        """Get detailed grade breakdown including per-category calculations."""
        final_grade = self.calculate_course_grade(course_id)

        self.cursor.execute('''
            SELECT 
                c.category_name,
                c.weight,
                COALESCE(SUM(a.earned_points), 0) as earned,
                COALESCE(SUM(a.max_points), 0) as possible,
                COUNT(a.assignment_id) as assignment_count
            FROM categories c
            LEFT JOIN assignments a ON c.category_id = a.category_id
            WHERE c.course_id = ? AND LOWER(c.category_name) != 'unallocated'
            GROUP BY c.category_id
            ORDER BY c.category_name
        ''', (course_id,))

        categories = []
        for name, weight, earned, possible, count in self.cursor.fetchall():
            grade = (earned / possible * 100) if possible > 0 else 0
            categories.append({
                'name': name,
                'weight': weight,
                'grade': grade,
                'earned': earned,
                'possible': possible,
                'assignment_count': count
            })

        return {
            'final_grade': final_grade,
            'categories': categories,
            'timestamp': datetime.now().isoformat()
        }

    def get_course_assignments(self, course_id: int):
        """Get all assignments for a course with their details."""
        self.cursor.execute('''
        SELECT a.title, a.max_points, a.earned_points, a.entry_date,
               c.category_name, c.weight,
               (a.earned_points / a.max_points * c.weight) as weighted_points
        FROM assignments a
        JOIN categories c ON a.category_id = c.category_id
        WHERE a.course_id = ?
        ORDER BY a.entry_date DESC
        ''', (course_id,))
        return self.cursor.fetchall()

    
    def get_assignment_by_id(self, assignment_id: int) -> dict:
        """Get details of a specific assignment."""
        self.cursor.execute('''
        SELECT a.assignment_id, a.title, a.max_points, a.earned_points, 
               a.entry_date, c.category_name, co.course_code, co.semester
        FROM assignments a
        JOIN categories c ON a.category_id = c.category_id
        JOIN courses co ON a.course_id = co.course_id
        WHERE a.assignment_id = ?
        ''', (assignment_id,))
        row = self.cursor.fetchone()

        if not row:
            raise GradeBookError(f"Assignment with ID {assignment_id} not found")

        return {
            "assignment_id": row[0],
            "title": row[1],
            "max_points": row[2],
            "earned_points": row[3],
            "entry_date": row[4],
            "category_name": row[5],
            "course_code": row[6],
            "semester": row[7],
        }

    def get_course_categories(self, course_id: int):
        """Get all categories and their weights for a course."""
        self.cursor.execute('''
        SELECT category_name, weight
        FROM categories
        WHERE course_id = ?
        ORDER BY category_name
        ''', (course_id,))
        return self.cursor.fetchall()


    def get_category_by_id(self, category_id: int) -> dict:
        """Get details of a specific category."""
        self.cursor.execute('''
        SELECT category_id, category_name, weight, course_id 
        FROM categories 
        WHERE category_id = ?
        ''', (category_id,))
        row = self.cursor.fetchone()

        if not row:
            raise GradeBookError(f"Category with ID {category_id} not found")

        return {
            "category_id": row[0],
            "category_name": row[1],
            "weight": row[2],
            "course_id": row[3],
        }

    def get_category_grade(self, category_id: int) -> tuple[float, float]:
        """Calculate grade for a single category, properly handling extra credit."""
        self.cursor.execute('''
            SELECT 
                SUM(earned_points) as total_earned,
                SUM(max_points) as total_possible
            FROM assignments
            WHERE category_id = ?
        ''', (category_id,))

        earned, possible = self.cursor.fetchone()
        if possible == 0:
            return 0.0, 0.0

        # Ensure we're working with actual numbers, not None
        earned = earned or 0.0
        possible = possible or 0.0

        return earned, possible

    def get_course_id_by_code(self, course_code: str, semester: str = None) -> int:
        """Get course ID by course code and optionally semester."""
        if semester:
            self.cursor.execute('''
            SELECT course_id FROM courses 
            WHERE course_code = ? AND semester = ?
            ''', (course_code.upper(), semester))
        else:
            self.cursor.execute('''
            SELECT course_id, semester FROM courses 
            WHERE course_code = ?
            ''', (course_code.upper(),))

        rows = self.cursor.fetchall()
        if not rows:
            raise GradeBookError(f"Course '{course_code}' not found")
        if len(rows) > 1:
            semesters = [row[1] for row in rows]
            raise GradeBookError(
                f"Multiple sections of {course_code} found. "
                f"Please specify semester. Available: {', '.join(semesters)}"
            )
        return rows[0][0] if semester else rows[0][0]

    def get_category_id(self, course_code: str, category_name: str, semester: str = None) -> int:
        """Get category ID by course code and category name."""
        course_id = self.get_course_id_by_code(course_code, semester)

        self.cursor.execute('''
        SELECT category_id FROM categories 
        WHERE course_id = ? AND category_name = ?
        ''', (course_id, category_name))

        result = self.cursor.fetchone()
        if not result:
            raise GradeBookError(
                f"Category '{category_name}' not found in {course_code}"
            )
        return result[0]

    def get_assignment_id(self, course_code: str, title: str, semester: str = None) -> int:
        """Get assignment ID by course code and assignment title."""
        course_id = self.get_course_id_by_code(course_code, semester)

        self.cursor.execute('''
        SELECT assignment_id FROM assignments 
        WHERE course_id = ? AND title = ?
        ''', (course_id, title))

        rows = self.cursor.fetchall()
        if not rows:
            raise GradeBookError(
                f"Assignment '{title}' not found in {course_code}"
            )
        if len(rows) > 1:
            raise GradeBookError(
                f"Multiple assignments found with title '{title}' in {course_code}. "
                "Please ensure assignment titles are unique within a course."
            )
        return rows[0][0]


    def update_assignment(self, assignment_id: int, title: str = None,
                          max_points: float = None, earned_points: float = None, category_id: int = None):
        """Update assignment details."""
        updates = []
        params = []
        if title:
            updates.append("title = ?")
            params.append(title)
        if max_points is not None:
            updates.append("max_points = ?")
            params.append(max_points)
        if earned_points is not None:
            updates.append("earned_points = ?")
            params.append(earned_points)
        if category_id:
            updates.append("category_id = ?")
            params.append(category_id)

        if updates:
            query = f"UPDATE assignments SET {', '.join(updates)} WHERE assignment_id = ?"
            params.append(assignment_id)
            self.cursor.execute(query, tuple(params))
            self.conn.commit()

   
    def remove_assignment(self, assignment_id: int):
        """Remove an assignment by its ID."""
        self.cursor.execute('''
        DELETE FROM assignments WHERE assignment_id = ?
        ''', (assignment_id,))
        self.conn.commit()


    def get_course_summary(self, course_id: int) -> dict:
        """Get a summary of a course with grades and categories."""
        self.cursor.execute('SELECT course_code, course_title, semester FROM courses WHERE course_id = ?', (course_id,))
        course_info = self.cursor.fetchone()

        if not course_info:
            raise GradeBookError("Course not found")

        categories = self.get_course_categories(course_id)
        assignments = self.get_course_assignments(course_id)
        final_grade = self.calculate_course_grade(course_id)

        return {
            "course_code": course_info[0],
            "course_title": course_info[1],
            "semester": course_info[2],
            "categories": categories,
            "assignments": assignments,
            "final_grade": final_grade,
        }

    def close(self):
        """Close the database connection."""
        self.conn.close()

def main_production():
    try:
        db_path = Path("~/.gradebook/gradebook.db").expanduser()
        initialize_database(db_path)
    except Exception as e:
        print(f"Error initializing database: {str(e)}")
        raise

def initialize_database(db_path):
    try:
        print(f"Initializing database at {db_path}")

        gradebook = Gradebook(db_path)

        print("\nAdding courses...")

        # Add your Fall 2024 courses
        bio_seminar = gradebook.add_course("BIO302", "Biology Seminar", "Fall 2024")
        evolution = gradebook.add_course("BIO515", "Evolution", "Fall 2024")
        immuno_lecture = gradebook.add_course("BIO511", "Immunology (Lecture)", "Fall 2024")
        immuno_lab = gradebook.add_course("BIO511-L", "Immunology (Lab)", "Fall 2024")
        biochem = gradebook.add_course("CHM352", "Introduction to Biochemistry", "Fall 2024")
        orgo = gradebook.add_course("CHM343", "Organic Chemistry II", "Fall 2024")

        # Example category weights for each course
        # Note: You should adjust these weights according to your course syllabi

        # Biology Seminar categories
        bio_seminar_categories = [
            ("Participation", 0.20),
            ("Presentations", 0.40),
            ("Assignments", 0.40)
        ]
        gradebook.add_categories(bio_seminar, bio_seminar_categories)

        # Evolution categories
        evolution_categories = [
            ("Exams", 0.50),
            ("Lab Reports", 0.30),
            ("Homework", 0.20)
        ]
        gradebook.add_categories(evolution, evolution_categories)

        # Immunology Lecture categories
        immuno_lecture_categories = [
            ("Exams", 0.60),
            ("Quizzes", 0.25),
            ("Homework", 0.15)
        ]
        gradebook.add_categories(immuno_lecture, immuno_lecture_categories)

        # Immunology Lab categories
        immuno_lab_categories = [
            ("Lab Reports", 0.60),
            ("Lab Participation", 0.20),
            ("Lab Practical", 0.20)
        ]
        gradebook.add_categories(immuno_lab, immuno_lab_categories)

        # Biochemistry categories
        biochem_categories = [
            ("Exams", 0.50),
            ("Quizzes", 0.25),
            ("Homework", 0.25)
        ]
        gradebook.add_categories(biochem, biochem_categories)

        # Organic Chemistry II categories
        orgo_categories = [
            ("Exams", 0.55),
            ("Lab Reports", 0.25),
            ("Homework", 0.20)
        ]
        gradebook.add_categories(orgo, orgo_categories)

        # Print all courses and their category weights
        courses = [
            (bio_seminar, "Biology Seminar"),
            (evolution, "Evolution"),
            (immuno_lecture, "Immunology Lecture"),
            (immuno_lab, "Immunology Lab"),
            (biochem, "Biochemistry"),
            (orgo, "Organic Chemistry II")
        ]

        for course_id, course_title in courses:
            print(f"\n{course_title} Categories:")
            for name, weight in gradebook.get_course_categories(course_id):
                print(f"- {name}: {weight * 100}%")

        gradebook.close()
    except GradeBookError as e:
        print(f"Error initializing database: {str(e)}")
        raise

if __name__ == "__main__":
    main_production()
