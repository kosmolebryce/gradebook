# gradebook/cli.py

import importlib.metadata
import statistics
import sys
import warnings
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import List, Tuple

import click
from rich.console import Group, Console, Text
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich import box
from rich import print as rprint

from gradebook.db import Gradebook

def create_styled_table(title: str) -> Table:
    """Create a consistently styled table with neutral background."""
    return Table(
        title=title,
        box=box.ROUNDED,
        style="white on grey11",        # Very dark grey background
        header_style="bold cyan",       # Keep colored headers
        title_style="bold white"        # Clean white title
    )

def deprecated(message):
    """Decorator to mark commands as deprecated."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            warnings.warn(message, DeprecationWarning, stacklevel=2)
            return f(*args, **kwargs)
        return wrapper
    return decorator

console = Console()

DEFAULT_DB_PATH = Path("~/.gradebook/gradebook.db").expanduser()
if not DEFAULT_DB_PATH.exists():
    DEFAULT_DB_PATH.mkdir(parents=True, exist_ok=True)

def format_percentage(value: float) -> str:
    """Format a decimal to percentage with 2 decimal places."""
    return f"{value * 100:.2f}%"

def get_version():
    """Get version from Poetry's package metadata."""
    try:
        return importlib.metadata.version("gradebook")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


class GradeBookCLI:
    def __init__(
            self,
            db_path=None,
            existing_db=None
    ) -> None:
        db_path = db_path or DEFAULT_DB_PATH

        if existing_db is not None:
            self.gradebook = existing_db
            self.db_path = existing_db.db_path
        else:
            if isinstance(db_path, str):
                db_path = Path(db_path)
            self.db_path = db_path
            self.gradebook = Gradebook(self.db_path)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self, 'gradebook'):
            self.gradebook.close()


@click.group()
@click.version_option(version=get_version(), prog_name="gradebook")
@click.option("--db-path",
              default=None,
              help="Specify the path to the database."
              )
@click.pass_context
def cli(ctx, db_path):
    """Main entry point"""
    db_path = db_path or DEFAULT_DB_PATH
    ctx.ensure_object(dict)
    ctx.obj = GradeBookCLI(db_path=db_path)


@cli.group()
def add():
    """Add items to the gradebook"""
    pass


@add.command('course')
@click.argument('course_code')
@click.argument('course_title')
@click.argument('semester')
@click.option('--credits', '-c', type=int, default=3, help="Number of credit hours (default: 3)")
@click.pass_obj
def add_course(gradebook: GradeBookCLI, course_code: str, course_title: str, semester: str, credits: int):
    """Add a new course to the gradebook."""
    try:
        cursor = gradebook.gradebook.cursor

        # First check if course already exists
        cursor.execute("""
            SELECT course_id FROM courses 
            WHERE course_code = ? AND semester = ?
        """, (course_code, semester))

        if cursor.fetchone():
            console.print(f"[yellow]Course {course_code} already exists for {semester}![/yellow]")
            return

        # Add the course with credit hours
        cursor.execute("""
            INSERT INTO courses (course_code, course_title, semester, credit_hours) 
            VALUES (?, ?, ?, ?)
        """, (course_code, course_title, semester, credits))

        gradebook.gradebook.conn.commit()

        # Verify the course was added
        cursor.execute("""
            SELECT course_id FROM courses 
            WHERE course_code = ? AND semester = ?
        """, (course_code, semester))

        result = cursor.fetchone()
        if result:
            course_id = result[0]
            console.print(f"[green]Successfully added course:[/green] {course_code}: {course_title} ({semester}, {credits} credits)")
            console.print(f"[dim]Debug: Added course with ID {course_id}[/dim]")
            console.print(f"Now add categories with: gradebook add categories {course_code}")
        else:
            console.print("[red]Failed to verify course was added![/red]")

    except Exception as e:
        gradebook.gradebook.conn.rollback()
        console.print(f"[red]Error adding course:[/red] {str(e)}")


@add.command('categories')
@click.argument('course_code')
@click.option('--semester', help="Specify semester if course exists in multiple semesters")
@click.pass_obj
def add_categories(gradebook: GradeBookCLI, course_code: str, semester: str):
    """Add or update categories for a course while preserving existing assignments."""
    try:
        cursor = gradebook.gradebook.cursor
        course_id = gradebook.gradebook.get_course_id_by_code(course_code)

        # Check for existing categories and assignments
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT c.category_id) as category_count,
                COUNT(DISTINCT a.assignment_id) as assignment_count,
                COALESCE(SUM(c.weight), 0) as total_weight
            FROM categories c
            LEFT JOIN assignments a ON c.category_id = a.category_id
            WHERE c.course_id = ?
        """, (course_id,))

        category_count, assignment_count, current_weight = cursor.fetchone()

        if category_count > 0:
            # Show current categories if they exist
            cursor.execute("""
                SELECT c.category_name, c.weight, COUNT(a.assignment_id) as assignment_count
                FROM categories c
                LEFT JOIN assignments a ON c.category_id = a.category_id
                WHERE c.course_id = ?
                GROUP BY c.category_id
                ORDER BY c.category_name
            """, (course_id,))

            current_categories = cursor.fetchall()

            table = create_styled_table(
                    title="\nCurrent Categories",
                    )
            table.add_column("Category", style="cyan")
            table.add_column("Weight", style="magenta")
            table.add_column("Assignments", justify="right")

            for name, weight, count in current_categories:
                table.add_row(name, f"{weight * 100:.1f}%", str(count))

            console.print(table)

            if not Confirm.ask("Do you want to update these categories?"):
                return

            if assignment_count > 0:
                console.print(f"\n[yellow]Note: {assignment_count} existing assignments will be preserved[/yellow]")

            # Create temporary category for assignments if needed
            if assignment_count > 0:
                cursor.execute("""
                    INSERT INTO categories (course_id, category_name, weight)
                    VALUES (?, '_temp_category_', 0.0)
                """, (course_id,))
                temp_category_id = cursor.lastrowid

                # Move all assignments to temporary category
                cursor.execute("""
                    UPDATE assignments
                    SET category_id = ?
                    WHERE category_id IN (
                        SELECT category_id FROM categories WHERE course_id = ? AND category_name != '_temp_category_'
                    )
                """, (temp_category_id, course_id))

            # Delete old categories (except temporary)
            cursor.execute("""
                DELETE FROM categories 
                WHERE course_id = ? AND category_name != '_temp_category_'
            """, (course_id,))

        # Collect new categories
        categories = []
        total_weight = 0.0

        while total_weight <= 1.0:
            remaining = 1.0 - total_weight
            console.print(f"\nRemaining weight available: [cyan]{format_percentage(remaining)}[/cyan]")

            name = Prompt.ask("Enter category name (or 'done' if finished)")
            if name.lower() == 'done':
                if abs(1.0 - total_weight) > 0.0001:
                    console.print("[yellow]Warning: Total weights do not sum to 100%[/yellow]")
                    if not Confirm.ask("Continue anyway?"):
                        continue
                break

            weight = float(Prompt.ask("Enter weight (as decimal)", default="0.25"))
            if weight > remaining + 0.0001:
                console.print("[red]Error: Weight would exceed 100%[/red]")
                continue

            categories.append((name, weight))
            total_weight += weight

            if abs(total_weight - 1.0) <= 0.0001:
                break

        if categories:
            try:
                # Add new categories
                for name, weight in categories:
                    cursor.execute("""
                        INSERT INTO categories (course_id, category_name, weight)
                        VALUES (?, ?, ?)
                    """, (course_id, name, weight))

                # If there were existing assignments, distribute them
                if assignment_count > 0:
                    # Get the first category as default
                    cursor.execute("""
                        SELECT category_id, category_name FROM categories 
                        WHERE course_id = ? AND category_name != '_temp_category_'
                        LIMIT 1
                    """, (course_id,))
                    default_category = cursor.fetchone()

                    if default_category:
                        default_category_id, default_category_name = default_category

                        # Move assignments to the default category
                        cursor.execute("""
                            UPDATE assignments
                            SET category_id = ?
                            WHERE category_id IN (
                                SELECT category_id FROM categories 
                                WHERE course_id = ? AND category_name = '_temp_category_'
                            )
                        """, (default_category_id, course_id))

                        console.print(
                            f"\n[yellow]Note: Existing assignments have been moved to '{default_category_name}'[/yellow]")
                        console.print("[yellow]Use 'gradebook move assignment' to redistribute them as needed[/yellow]")

                    # Delete temporary category
                    cursor.execute("""
                        DELETE FROM categories 
                        WHERE course_id = ? AND category_name = '_temp_category_'
                    """, (course_id,))

                gradebook.gradebook.conn.commit()
                console.print("[green]Successfully updated categories![/green]")

                table = create_styled_table(title="\nNew Categories")
                table.add_column("Category", style="cyan")
                table.add_column("Weight", justify="right", style="magenta")

                for name, weight in categories:
                    table.add_row(name, format_percentage(weight))

                console.print(table)

            except Exception as e:
                gradebook.gradebook.conn.rollback()
                console.print(f"[red]Error updating categories:[/red] {str(e)}")

    except Exception as e:
        console.print(f"[red]Error:[/red] {str(e)}")

@add.command('category')
@click.argument('course_code')
@click.argument('category_name')
@click.argument('weight', type=float)
@click.option('--semester', help="Specify semester if course exists in multiple semesters")
@click.pass_obj
def add_category(gradebook: GradeBookCLI, course_code: str, category_name: str, 
                 weight: float, semester: str):
    """Add a single category to a course (weight must come from Unallocated)."""
    try:
        cursor = gradebook.gradebook.cursor
        course_id = gradebook.gradebook.get_course_id_by_code(course_code, semester)
        tolerance = 0.0001  # Same tolerance as in db.py

        # Verify category doesn't already exist
        cursor.execute("""
            SELECT category_name, weight 
            FROM categories 
            WHERE course_id = ? AND category_name = ?
        """, (course_id, category_name))

        if cursor.fetchone():
            console.print(f"[red]Error: Category '{category_name}' already exists in this course[/red]")
            return

        if category_name.lower() == "unallocated":
            console.print("[red]Error: Cannot explicitly add an 'Unallocated' category[/red]")
            return

        if weight <= 0:
            console.print("[red]Error: Weight must be greater than 0[/red]")
            return

        # Look for Unallocated category
        cursor.execute("""
            SELECT category_id, weight 
            FROM categories 
            WHERE course_id = ? AND LOWER(category_name) = 'unallocated'
        """, (course_id,))

        unallocated = cursor.fetchone()
        if not unallocated:
            console.print("[red]Error: No Unallocated weight available[/red]")
            return

        unallocated_id, unallocated_weight = unallocated
        weight_difference = weight - unallocated_weight
        
        if weight_difference > tolerance:  # Changed comparison
            console.print(f"[red]Error: Not enough weight in Unallocated category "
                          f"(has {unallocated_weight * 100:.1f}%, needs {weight * 100:.1f}%)[/red]")
            return

        # If we get here, update the categories
        if abs(weight_difference) <= tolerance:
            # Weights match exactly - remove Unallocated
            cursor.execute("DELETE FROM categories WHERE category_id = ?", (unallocated_id,))
        else:
            # Update Unallocated with remaining weight
            new_unallocated = unallocated_weight - weight
            cursor.execute("""
                UPDATE categories 
                SET weight = ?
                WHERE category_id = ?
            """, (new_unallocated, unallocated_id))

        # Add the new category
        cursor.execute("""
            INSERT INTO categories (course_id, category_name, weight)
            VALUES (?, ?, ?)
        """, (course_id, category_name, weight))

        gradebook.gradebook.conn.commit()

        # Show updated categories
        cursor.execute("""
            SELECT category_name, weight
            FROM categories
            WHERE course_id = ?
            ORDER BY 
                CASE WHEN LOWER(category_name) = 'unallocated' THEN 1 ELSE 0 END,
                category_name
        """, (course_id,))

        categories = cursor.fetchall()

        table = create_styled_table(title=f"\nCategories for {course_code}")
        table.add_column("Category", style="cyan")
        table.add_column("Weight", justify="right", style="green")

        for cat_name, cat_weight in categories:
            table.add_row(cat_name, f"{cat_weight * 100:.1f}%")

        console.print(table)

    except Exception as e:
        gradebook.gradebook.conn.rollback()
        console.print(f"[red]Error adding category:[/red] {str(e)}")


@add.command('assignment')
@click.argument('course_code')
@click.argument('category_name')
@click.argument('title')
@click.argument('max_points', type=float)
@click.argument('earned_points', type=float)
@click.pass_obj
def add_assignment(gradebook: GradeBookCLI, course_code: str, category_name: str,
                   title: str, max_points: float, earned_points: float):
    """Add a new assignment to a course category.

    Example:
        gradebook add assignment CHM343 "Homework" "Lab Report 1" 100 85
        gradebook add assignment CHM343 "Homework" "Extra Credit Lab" 10 12
    """
    try:
        cursor = gradebook.gradebook.cursor

        # Basic validation
        if max_points <= 0:
            console.print(f"[red]Error:[/red] Max points must be greater than 0")
            return

        if earned_points < 0:
            console.print(f"[red]Error:[/red] Earned points cannot be negative")
            return

        # Get course ID and validate category
        course_id = gradebook.gradebook.get_course_id_by_code(course_code)
        category_id = gradebook.gradebook.get_category_id(course_code, category_name)

        # Add the assignment
        assignment_id = gradebook.gradebook.add_assignment(
            course_id, category_id, title, max_points, earned_points
        )

        percentage = (earned_points / max_points) * 100
        extra_credit = earned_points > max_points

        # Create success message with extra credit indication
        message = f"""[green]Successfully added assignment![/green]
Course: {course_code}
Category: {category_name}
Title: {title}
Score: {earned_points}/{max_points} ({percentage:.2f}%)"""

        if extra_credit:
            message += f"\n[yellow]Extra Credit![/yellow] Earned {earned_points - max_points} points above maximum"

        console.print(Panel(message,
                            title="New Assignment",
                            border_style="green"
                            ))

        # Show updated course grade
        overall_grade = gradebook.gradebook.calculate_course_grade(course_id)
        console.print(f"\nUpdated course grade: [bold magenta]{overall_grade:.2f}%[/bold magenta]")

    except Exception as e:
        console.print(f"[red]Error adding assignment:[/red] {str(e)}")

@cli.group()
def remove():
    """Remove items from the gradebook"""
    pass

@remove.command('course')
@click.argument('course_code')
@click.option('--semester', help="Specify semester if course exists in multiple semesters")
@click.option('--force', is_flag=True, help="Skip confirmation prompt")
@click.pass_obj
def remove_course(gradebook: GradeBookCLI, course_code: str, semester: str, force: bool):
    """Remove a course by course code."""
    try:
        cursor = gradebook.gradebook.cursor
        course_id = gradebook.gradebook.get_course_id_by_code(course_code, semester)

        cursor.execute("""
        SELECT course_title, semester, 
               (SELECT COUNT(*) FROM assignments WHERE course_id = c.course_id) as assignment_count
        FROM courses c
        WHERE course_id = ?
        """, (course_id,))

        course_title, semester, assignment_count = cursor.fetchone()

        if not force:
            console.print(f"[yellow]Warning: This will remove the course '[bold]{course_code}: {course_title}[/bold]' "
                          f"({semester}) and all its categories and {assignment_count} assignment(s)![/yellow]")
            if not Confirm.ask("Are you sure you want to proceed?"):
                console.print("Operation cancelled.")
                return

        cursor.execute("DELETE FROM courses WHERE course_id = ?", (course_id,))
        gradebook.gradebook.conn.commit()

        console.print(f"[green]Successfully removed course: {course_code}: {course_title} ({semester})[/green]")

    except Exception as e:
        console.print(f"[red]Error removing course:[/red] {str(e)}")

@remove.command('category')
@click.argument('course_code')
@click.argument('category_name')
@click.option('--semester', help="Specify semester if course exists in multiple semesters")
@click.option('--force', is_flag=True, help="Skip confirmation prompt")
@click.option('--delete-assignments', is_flag=True, help="Delete assignments instead of preserving them")
@click.pass_obj
def remove_category(gradebook: GradeBookCLI, course_code: str, category_name: str,
                    semester: str, force: bool, delete_assignments: bool):
    """Remove a category by name."""
    try:
        course_id = gradebook.gradebook.get_course_id_by_code(course_code, semester)
        category_id = gradebook.gradebook.get_category_id(course_id, category_name)

        cursor = gradebook.gradebook.cursor
        cursor.execute("""
            SELECT COUNT(a.assignment_id) as assignment_count
            FROM categories cat
            LEFT JOIN assignments a ON cat.category_id = a.category_id
            WHERE cat.category_id = ?
            GROUP BY cat.category_id
        """, (category_id,))

        assignment_count = cursor.fetchone()[0]

        if not force:
            if delete_assignments:
                console.print(f"[yellow]Warning: This will remove the category '[bold]{category_name}[/bold]' "
                              f"from course '{course_code}' and permanently delete its {assignment_count} assignment(s)![/yellow]")
            else:
                console.print(f"[yellow]Warning: This will remove the category '[bold]{category_name}[/bold]' "
                              f"from course '{course_code}'. {assignment_count} assignment(s) will be moved to 'Unassigned'.[/yellow]")

            if not Confirm.ask("Are you sure you want to proceed?"):
                console.print("Operation cancelled.")
                return

        removed_name, affected_count = gradebook.gradebook.remove_category(
            category_id,
            preserve_assignments=not delete_assignments
        )

        if delete_assignments:
            console.print(
                f"[green]Successfully removed category '{removed_name}' and {affected_count} assignment(s)[/green]")
        else:
            console.print(f"[green]Successfully removed category '{removed_name}'. "
                          f"{affected_count} assignment(s) moved to 'Unassigned'[/green]")

    except Exception as e:
        console.print(f"[red]Error removing category:[/red] {str(e)}")


@remove.command('assignment')
@click.argument('course_code')
@click.argument('assignment_title')
@click.option('--semester', help="Specify semester if course exists in multiple semesters")
@click.option('--force', is_flag=True, help="Skip confirmation prompt")
@click.pass_obj
def remove_assignment(cli: GradeBookCLI, course_code: str, assignment_title: str,
                      semester: str = None, force: bool = False):
    """Remove an assignment by name.

    Example:
        gradebook remove assignment CHM343 "Lab Report 1"
        gradebook remove assignment CHM343 "Lab Report 1" --semester "Fall 2024"
    """
    try:
        # Get course_id using the existing helper method that handles semester logic
        course_id = cli.gradebook.get_course_id_by_code(course_code, semester)

        cursor = cli.gradebook.cursor
        cursor.execute("""
            SELECT a.assignment_id, a.earned_points, a.max_points, cat.category_name,
                   c.semester
            FROM assignments a
            JOIN categories cat ON a.category_id = cat.category_id
            JOIN courses c ON a.course_id = c.course_id
            WHERE c.course_id = ? AND a.title = ?
        """, (course_id, assignment_title))

        result = cursor.fetchone()
        if not result:
            console.print(f"[red]Assignment '{assignment_title}' not found in {course_code}[/red]")
            return

        assignment_id, earned_points, max_points, category_name, course_semester = result

        if not force:
            console.print(f"[yellow]Warning: This will remove the assignment '[bold]{assignment_title}[/bold]' "
                          f"({earned_points}/{max_points}) from {category_name} in {course_code} "
                          f"({course_semester})![/yellow]")
            if not Confirm.ask("Are you sure you want to proceed?"):
                console.print("Operation cancelled.")
                return

        cursor.execute("DELETE FROM assignments WHERE assignment_id = ?", (assignment_id,))
        cli.gradebook.conn.commit()

        console.print(f"[green]Successfully removed assignment: {assignment_title}[/green]")

        # Show updated course grade
        try:
            overall_grade = cli.gradebook.calculate_course_grade(course_id)
            console.print(f"\nUpdated course grade: [bold magenta]{overall_grade:.2f}%[/bold magenta]")
        except Exception as e:
            pass  # Don't show grade if there are no assignments left

    except Exception as e:
        console.print(f"[red]Error removing assignment:[/red] {str(e)}")


@cli.group()
def view():
    """View and analyze gradebook data"""
    pass

@view.command('assignment')
@click.argument('course_code')
@click.argument('assignment_title')
@click.option('--semester', help="Specify semester if course exists in multiple semesters")
@click.pass_obj
def view_assignment(gradebook: GradeBookCLI, course_code: str, assignment_title: str, semester: str):
    """Display detailed information about a specific assignment.

    Example:
        gradebook show assignment CHM343 "Lab Report 1"
    """
    try:
        course_id = gradebook.gradebook.get_course_id_by_code(course_code, semester)
        assignment_id = gradebook.gradebook.get_assignment_id(course_code, assignment_title, semester)

        cursor = gradebook.gradebook.cursor
        cursor.execute("""
            SELECT 
                a.title,
                a.max_points,
                a.earned_points,
                a.entry_date,
                c.category_name,
                c.weight,
                co.course_title,
                co.semester
            FROM assignments a
            JOIN categories c ON a.category_id = c.category_id
            JOIN courses co ON a.course_id = co.course_id
            WHERE a.assignment_id = ?
        """, (assignment_id,))

        result = cursor.fetchone()
        if not result:
            console.print(f"[red]Assignment not found![/red]")
            return

        title, max_points, earned_points, entry_date, category, weight, course_title, semester = result

        # Calculate scores
        percentage = (earned_points / max_points) * 100
        weighted_score = (earned_points / max_points) * weight * 100

        # Format date
        formatted_date = datetime.strptime(entry_date, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d %I:%M %p')

        # Create layout for display
        layout = Layout()
        layout.split_column(
            Layout(name="header"),
            Layout(name="details"),
            Layout(name="stats")
        )

        # Header with course info
        layout["header"].update(Panel(
            f"[bold blue]{course_code}: {course_title}[/bold blue]\n{semester}",
            title="Course Information"
        ))

        # Assignment details
        details = f"""[bold]Assignment:[/bold] {title}
[bold]Category:[/bold] {category} (Weight: {weight * 100:.1f}%)
[bold]Date Entered:[/bold] {formatted_date}"""

        layout["details"].update(Panel(
            details,
            title="Assignment Details"
        ))

        # Grade statistics
        stats = f"""[bold]Score:[/bold] {earned_points}/{max_points}
[bold]Percentage:[/bold] {percentage:.2f}%
[bold]Weighted Score:[/bold] {weighted_score:.2f}%"""

        # Add color coding for grade
        if percentage >= 90:
            stats = f"[green]{stats}[/green]"
        elif percentage >= 80:
            stats = f"[blue]{stats}[/blue]"
        elif percentage >= 70:
            stats = f"[yellow]{stats}[/yellow]"
        else:
            stats = f"[red]{stats}[/red]"

        layout["stats"].update(Panel(
            stats,
            title="Grade Information"
        ))

        console.print(layout)

    except Exception as e:
        console.print(f"[red]Error displaying assignment:[/red] {str(e)}")


@view.command('assignments')
@click.argument('course_code')
@click.option('--semester', help="Specify semester if course exists in multiple semesters")
@click.option('--sort', type=click.Choice(['date', 'grade', 'category']), default='date',
              help="Sort assignments by date, grade, or category (default: date)")
@click.option('--reverse', is_flag=True, help="Reverse the sort order")
@click.pass_obj
def view_assignments(gradebook: GradeBookCLI, course_code: str, semester: str, sort: str, reverse: bool):
    """Display all assignments for a course with grades and statistics.

    Example:
        gradebook view assignments CHM343
        gradebook view assignments CHM343 --sort grade --reverse
        gradebook view assignments CHM343 --semester "Fall 2024"
    """
    try:
        cursor = gradebook.gradebook.cursor
        course_id = gradebook.gradebook.get_course_id_by_code(course_code, semester)

        # Get course info
        cursor.execute("""
            SELECT course_title, semester
            FROM courses 
            WHERE course_id = ?
        """, (course_id,))

        course_title, course_sem = cursor.fetchone()

        # Get all assignments with category info
        query = """
            SELECT 
                a.title,
                a.earned_points,
                a.max_points,
                a.entry_date,
                c.category_name,
                c.weight,
                (a.earned_points / a.max_points * 100) as percentage
            FROM assignments a
            JOIN categories c ON a.category_id = c.category_id
            WHERE a.course_id = ?
        """

        # Add sorting
        if sort == 'date':
            query += " ORDER BY a.entry_date"
        elif sort == 'grade':
            query += " ORDER BY percentage"
        else:  # category
            query += " ORDER BY c.category_name, a.entry_date"

        if reverse:
            query += " DESC"

        cursor.execute(query, (course_id,))
        assignments = cursor.fetchall()

        if not assignments:
            console.print(f"[yellow]No assignments found for {course_code}[/yellow]")
            return

        # Create header with course info
        console.print(Panel(
            f"[bold blue]{course_code}:[/bold blue] {course_title}\n"
            f"[cyan]Semester:[/cyan] {course_sem}",
            title="Course Assignments"
        ))

        # Create assignments table
        table = create_styled_table(title="\nAssignments")
        table.add_column("Date", style="dim")
        table.add_column("Assignment")
        table.add_column("Category", style="cyan")
        table.add_column("Weight", justify="right", style="magenta")
        table.add_column("Score", justify="right")
        table.add_column("Grade", justify="right")

        # Calculate statistics
        grades = []
        category_grades = {}

        for title, earned, max_points, date, category, weight, percentage in assignments:
            # Format date
            date_str = datetime.strptime(date, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d')

            # Format grade with color
            if percentage >= 90:
                grade_str = f"[green]{percentage:.1f}%[/green]"
            elif percentage >= 80:
                grade_str = f"[blue]{percentage:.1f}%[/blue]"
            elif percentage >= 70:
                grade_str = f"[yellow]{percentage:.1f}%[/yellow]"
            else:
                grade_str = f"[red]{percentage:.1f}%[/red]"

            table.add_row(
                date_str,
                title,
                category,
                f"{weight * 100:.1f}%",
                f"{earned}/{max_points}",
                grade_str
            )

            # Track statistics
            grades.append(percentage)
            if category not in category_grades:
                category_grades[category] = []
            category_grades[category].append(percentage)

        console.print(table)

        # Show statistics
        stats_table = create_styled_table(title="\nGrade Statistics")
        stats_table.add_column("Metric", style="cyan")
        stats_table.add_column("Value", style="green")

        stats_table.add_row("Total Assignments", str(len(grades)))
        stats_table.add_row("Overall Average", f"{statistics.mean(grades):.1f}%")
        stats_table.add_row("Highest Grade", f"{max(grades):.1f}%")
        stats_table.add_row("Lowest Grade", f"{min(grades):.1f}%")

        # Add category averages
        for category, cat_grades in category_grades.items():
            stats_table.add_row(
                f"{category} Average",
                f"{statistics.mean(cat_grades):.1f}%"
            )

        console.print(stats_table)

        # Show overall course grade
        try:
            overall_grade = gradebook.gradebook.calculate_course_grade(course_id)
            console.print(f"\nCurrent Course Grade: [bold magenta]{overall_grade:.1f}%[/bold magenta]")
        except Exception as e:
            console.print(f"[red]Error calculating overall grade: {str(e)}[/red]")

    except Exception as e:
        console.print(f"[red]Error displaying assignments:[/red] {str(e)}")


@view.command('course')
@click.argument('course_code')
@click.option('--semester', help="Specify semester if course exists in multiple semesters")
@click.pass_obj
def view_course(gradebook: GradeBookCLI, course_code: str, semester: str):
    """Show detailed information for a specific course."""
    try:
        course_id = gradebook.gradebook.get_course_id_by_code(course_code, semester)
        breakdown = gradebook.gradebook.get_grade_breakdown(course_id)

        # Create course summary layout
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="grades"),
            Layout(name="recent", size=10)
        )

        # Header with course info
        cursor = gradebook.gradebook.cursor
        cursor.execute("""
            SELECT course_title, semester
            FROM courses WHERE course_id = ?
        """, (course_id,))
        title, sem = cursor.fetchone()

        layout["header"].update(Panel(
            f"[bold blue]{course_code}:[/bold blue] {title}\n"
            f"[cyan]Semester:[/cyan] {sem}\n"
            f"[green]Overall Grade:[/green] {breakdown['final_grade']:.1f}%",
            title="Course Summary"
        ))

        # Grades breakdown
        table = create_styled_table(title="\nGrades - Breakdown") 
        table.add_column("Category", style="cyan")
        table.add_column("Weight", justify="right")
        table.add_column("Grade", justify="right", style="green")
        table.add_column("Items", justify="right")

        for cat in breakdown['categories']:
            table.add_row(
                cat['name'],
                f"{cat['weight'] * 100:.1f}%",
                f"{cat['grade']:.1f}%",
                str(cat['assignment_count'])
            )

        layout["grades"].update(Panel(table, title="Grade Breakdown"))

        # Recent assignments
        cursor.execute("""
            SELECT 
                a.title,
                a.earned_points,
                a.max_points,
                c.category_name,
                a.entry_date
            FROM assignments a
            JOIN categories c ON a.category_id = c.category_id
            WHERE a.course_id = ?
            ORDER BY a.entry_date DESC
            LIMIT 5
        """, (course_id,))

        recent = cursor.fetchall()
        if recent:
            recent_table = create_styled_table(title="\nRecents")
            recent_table.add_column("Date", style="dim")
            recent_table.add_column("Assignment")
            recent_table.add_column("Category", style="cyan")
            recent_table.add_column("Score", justify="right")

            for title, earned, max_points, category, date in recent:
                date_str = datetime.strptime(date, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d')
                score = (earned / max_points) * 100
                score_str = f"{earned}/{max_points} ({score:.1f}%)"
                recent_table.add_row(date_str, title, category, score_str)

            layout["recent"].update(Panel(recent_table, title="Recent Assignments"))

        console.print(layout)

    except Exception as e:
        console.print(f"[red]Error displaying course:[/red] {str(e)}")


@view.command('courses')
@click.option('--detailed', is_flag=True, help="Show detailed information")
@click.option('--semester', help="Filter by semester")
@click.pass_obj
def view_courses(gradebook: GradeBookCLI, detailed: bool, semester: str):
    """List all courses with grades and statistics."""
    try:
        cursor = gradebook.gradebook.cursor

        query = """
            SELECT 
                c.course_code, 
                c.course_title, 
                c.semester,
                c.credit_hours,
                COUNT(DISTINCT a.assignment_id) as assignment_count,
                COUNT(DISTINCT cat.category_id) as category_count
            FROM courses c
            LEFT JOIN assignments a ON c.course_id = a.course_id
            LEFT JOIN categories cat ON c.course_id = cat.course_id
            WHERE 1=1
        """
        params = []

        if semester:
            query += " AND c.semester = ?"
            params.append(semester)

        query += """ 
            GROUP BY c.course_id 
            ORDER BY c.semester DESC, c.course_code
        """

        cursor.execute(query, params)
        courses = cursor.fetchall()

        if not courses:
            console.print("[yellow]No courses found.[/yellow]")
            return

        if detailed:
            # Create detailed view with grade breakdowns
            for course in courses:
                code, title, sem, credits, assign_count, cat_count = course

                panel = Panel(
                    Group(
                        Text(f"[bold blue]{code}:[/bold blue] {title}"),
                        Text(f"[cyan]Semester:[/cyan] {sem}"),
                        Text(f"[magenta]Credits:[/magenta] {credits}"),
                        Text(f"[green]Categories:[/green] {cat_count}"),
                        Text(f"[yellow]Assignments:[/yellow] {assign_count}"),
                    ),
                    title=f"Course Details"
                )
                console.print(panel)

                if assign_count > 0:
                    try:
                        breakdown = gradebook.gradebook.get_grade_breakdown(code)
                        table = create_styled_table(title="\nGrade Breakdown")
                        table.add_column("Category", style="cyan")
                        table.add_column("Weight", justify="right")
                        table.add_column("Grade", justify="right", style="green")

                        for cat in breakdown['categories']:
                            table.add_row(
                                cat['name'],
                                f"{cat['weight'] * 100:.1f}%",
                                f"{cat['grade']:.1f}%"
                            )

                        console.print(table)
                        console.print(
                            f"\nOverall Grade: [bold magenta]{breakdown['final_grade']:.1f}%[/bold magenta]\n")
                    except Exception as e:
                        console.print(f"[yellow]Could not calculate grades: {str(e)}[/yellow]\n")
        else:
            # Create simple table view
            table = create_styled_table(title="\nCourses Overview")
            table.add_column("Code", style="cyan")
            table.add_column("Title")
            table.add_column("Semester")
            table.add_column("Items", justify="right")

            for course in courses:
                code, title, sem, assign_count, _ = course
                table.add_row(code, title, sem, str(assign_count))

            console.print(table)

    except Exception as e:
        console.print(f"[red]Error listing courses:[/red] {str(e)}")

@view.command('category')
@click.argument('course_code')
@click.argument('category_name')
@click.option('--semester', help="Specify semester if course exists in multiple semesters")
@click.pass_obj
def view_category(gradebook: GradeBookCLI, course_code: str, category_name: str, semester: str):
    """Display detailed information about a specific category.

    Example:
        gradebook show category CHM343 "Homework"
    """
    try:
        course_id = gradebook.gradebook.get_course_id_by_code(course_code, semester)
        category_id = gradebook.gradebook.get_category_id(course_code, category_name, semester)

        cursor = gradebook.gradebook.cursor
        cursor.execute("""
            SELECT 
                c.category_name,
                c.weight,
                co.course_title,
                co.semester,
                COUNT(a.assignment_id) as assignment_count,
                AVG(a.earned_points / a.max_points) as avg_score,
                MIN(a.earned_points / a.max_points) as min_score,
                MAX(a.earned_points / a.max_points) as max_score
            FROM categories c
            JOIN courses co ON c.course_id = co.course_id
            LEFT JOIN assignments a ON c.category_id = a.category_id
            WHERE c.category_id = ?
            GROUP BY c.category_id
        """, (category_id,))

        result = cursor.fetchone()
        if not result:
            console.print(f"[red]Category not found![/red]")
            return

        name, weight, course_title, semester, count, avg, min_score, max_score = result

        # Create summary table
        table = create_styled_table(title=f"\n{name} - Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="magenta")

        table.add_row("Course", f"{course_code}: {course_title}")
        table.add_row("Semester", semester)
        table.add_row("Weight", f"{weight * 100:.1f}%")
        table.add_row("Assignments", str(count))

        if count > 0:
            raw_score = avg * 100
            weighted_contribution = raw_score * weight
            table.add_row("Raw Score", f"{raw_score:.2f}%")
            table.add_row("Weighted Contribution", f"{weighted_contribution:.2f}%")
            table.add_row("Highest Assignment", f"{max_score * 100:.2f}%")
            table.add_row("Lowest Assignment", f"{min_score * 100:.2f}%")

        console.print(table)

        # If there are assignments, show them in detail
        if count > 0:
            cursor.execute("""
                SELECT 
                    a.title,
                    a.earned_points,
                    a.max_points,
                    a.entry_date,
                    (a.earned_points / a.max_points) as score
                FROM assignments a
                WHERE a.category_id = ?
                ORDER BY a.entry_date DESC
            """, (category_id,))

            assignments = cursor.fetchall()

            table = create_styled_table(title="\nAssignments")
            table.add_column("Title", style="cyan")
            table.add_column("Score", justify="right")
            table.add_column("Percentage", justify="right")
            table.add_column("Date", style="dim")

            for title, earned, max_points, date, score in assignments:
                percentage = score * 100
                score_str = f"{earned}/{max_points}"
                date_str = datetime.strptime(date, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d')

                # Color code the percentage
                if percentage >= 90:
                    percentage_str = f"[green]{percentage:.1f}%[/green]"
                elif percentage >= 80:
                    percentage_str = f"[blue]{percentage:.1f}%[/blue]"
                elif percentage >= 70:
                    percentage_str = f"[yellow]{percentage:.1f}%[/yellow]"
                else:
                    percentage_str = f"[red]{percentage:.1f}%[/red]"

                table.add_row(title, score_str, percentage_str, date_str)

            console.print("\n", table)

    except Exception as e:
        console.print(f"[red]Error displaying category:[/red] {str(e)}")


@view.command('details')
@click.argument('course_code')
@click.option('--semester', help="Specify semester if course exists in multiple semesters")
@click.pass_obj
def view_course_details(gradebook: GradeBookCLI, course_code: str, semester: str):
    """Display comprehensive course summary including all grades and statistics."""
    try:
        course_id = gradebook.gradebook.get_course_id_by_code(course_code, semester)
        summary = gradebook.gradebook.get_course_summary(course_id)

        # Create header with course info
        header = Panel(
            f"[bold blue]{summary['course_code']}:[/bold blue] {summary['course_title']}\n"
            f"[cyan]Semester:[/cyan] {summary['semester']}\n"
            f"[green]Overall Grade:[/green] {summary['final_grade']:.2f}%",
            title="Course Summary"
        )
        console.print(header)

        # Show category breakdown
        table = create_styled_table(title="\nCategory Details")
        table.add_column("Category", style="cyan")
        table.add_column("Weight", justify="right")
        table.add_column("Raw Score", justify="right", style="magenta")
        table.add_column("Weighted", justify="right", style="green")

        category_averages = {}
        for assignment in summary['assignments']:
            title, max_points, earned, date, category, weight, weighted = assignment
            if category not in category_averages:
                category_averages[category] = {'earned': 0, 'max': 0, 'weight': weight}
            cat = category_averages[category]
            cat['earned'] += earned
            cat['max'] += max_points

        for category, weight in summary['categories']:
            if category in category_averages:
                cat = category_averages[category]
                avg = (cat['earned'] / cat['max']) * 100
                weighted = avg * cat['weight']
                table.add_row(
                    category,
                    f"{cat['weight'] * 100:.1f}%",
                    f"{avg:.1f}%",
                    f"{weighted:.1f}%"
                )
            else:
                table.add_row(category, f"{weight * 100:.1f}%", "N/A", "N/A")

        console.print("\n", table)

        # Show recent assignments
        if summary['assignments']:
            table = create_styled_table(title="\nRecent Assignments")
            table.add_column("Date", style="dim")
            table.add_column("Category", style="cyan")
            table.add_column("Assignment")
            table.add_column("Score", justify="right")
            table.add_column("Grade", justify="right")

            for assignment in sorted(summary['assignments'], key=lambda x: x[3], reverse=True)[:5]:
                title, max_points, earned, date, category, weight, weighted = assignment
                percentage = (earned / max_points) * 100
                date_str = datetime.strptime(date, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d')

                # Color code the percentage
                if percentage >= 90:
                    grade_str = f"[green]{percentage:.1f}%[/green]"
                elif percentage >= 80:
                    grade_str = f"[blue]{percentage:.1f}%[/blue]"
                elif percentage >= 70:
                    grade_str = f"[yellow]{percentage:.1f}%[/yellow]"
                else:
                    grade_str = f"[red]{percentage:.1f}%[/red]"

                table.add_row(
                    date_str,
                    category,
                    title,
                    f"{earned}/{max_points}",
                    grade_str
                )

            console.print("\n", table)

        # Show grade statistics
        if summary['assignments']:
            grades = [(a[2] / a[1]) * 100 for a in summary['assignments']]
            stats = f"""
[bold]Grade Statistics:[/bold]
Highest Grade: {max(grades):.1f}%
Lowest Grade: {min(grades):.1f}%
Average Grade: {sum(grades) / len(grades):.1f}%
Total Assignments: {len(grades)}
"""
            console.print(Panel(stats, title="Statistics"))

    except Exception as e:
        console.print(f"[red]Error displaying course details:[/red] {str(e)}")

@view.command('trends')
@click.argument('course_code')
@click.option('--days', default=30, help="Number of days to analyze")
@click.pass_obj
def view_trends(gradebook: GradeBookCLI, course_code: str, days: int):
    """Show grade trends over time for a course."""
    try:
        cursor = gradebook.gradebook.cursor
        course_id = gradebook.gradebook.get_course_id_by_code(course_code)

        cursor.execute("SELECT course_title FROM courses WHERE course_id = ?", (course_id,))
        course_title = cursor.fetchone()[0]

        cursor.execute("""
            SELECT a.title, a.earned_points, a.max_points, a.entry_date,
                   c.category_name, c.weight
            FROM assignments a
            JOIN categories c ON a.category_id = c.category_id
            WHERE a.course_id = ?
            ORDER BY a.entry_date
        """, (course_id,))
        assignments = cursor.fetchall()

        if not assignments:
            console.print("[yellow]No assignments found for this course.[/yellow]")
            return

        dates = []
        grades = []
        running_grade = 0

        for title, earned, max_points, date, category, weight in assignments:
            score = (earned / max_points) * 100
            dates.append(date)
            grades.append(score)
            running_grade = statistics.mean(grades)

        layout = Layout()
        layout.split_column(
            Layout(name="title"),
            Layout(name="graph"),
            Layout(name="stats")
        )

        layout["title"].update(Panel(
            f"[bold blue]{course_title}[/bold blue] Grade Trends",
            style="white on blue"
        ))

        max_width = 60
        max_height = 15
        normalized_grades = [int((g / 100) * max_height) for g in grades]

        graph = ""
        for y in range(max_height, -1, -1):
            line = ""
            for grade in normalized_grades:
                if grade >= y:
                    line += "█"
                else:
                    line += " "
            graph += f"{100 * y / max_height:>3.0f}% |{line}\n"

        graph += "     " + "-" * len(grades) + "\n"
        graph += "     " + "Assignments Over Time"

        layout["graph"].update(Panel(graph, title="Grade History"))

        stats = f"""[green]Latest Grade:[/green] {grades[-1]:.1f}%
[cyan]Average Grade:[/cyan] {statistics.mean(grades):.1f}%
[magenta]Highest Grade:[/magenta] {max(grades):.1f}%
[yellow]Lowest Grade:[/yellow] {min(grades):.1f}%
[blue]Number of Assignments:[/blue] {len(grades)}"""

        layout["stats"].update(Panel(stats, title="Statistics"))

        console.print(layout)

    except Exception as e:
        console.print(f"[red]Error displaying trends:[/red] {str(e)}")

@view.command('distribution')
@click.argument('course_code')
@click.pass_obj
def view_distribution(gradebook: GradeBookCLI, course_code: str):
    """Show grade distribution for a course."""
    try:
        cursor = gradebook.gradebook.cursor
        course_id = gradebook.gradebook.get_course_id_by_code(course_code)

        cursor.execute("SELECT course_title FROM courses WHERE course_id = ?", (course_id,))
        course_title = cursor.fetchone()[0]

        cursor.execute("""
            SELECT (a.earned_points / a.max_points * 100) as percentage
            FROM assignments a
            WHERE a.course_id = ?
        """, (course_id,))
        grades = [row[0] for row in cursor.fetchall()]

        if not grades:
            console.print("[yellow]No grades found for this course.[/yellow]")
            return

        buckets = {
            'A (90-100)': 0,
            'B (80-89)': 0,
            'C (70-79)': 0,
            'D (60-69)': 0,
            'F (0-59)': 0
        }

        for grade in grades:
            if grade >= 90:
                buckets['A (90-100)'] += 1
            elif grade >= 80:
                buckets['B (80-89)'] += 1
            elif grade >= 70:
                buckets['C (70-79)'] += 1
            elif grade >= 60:
                buckets['D (60-69)'] += 1
            else:
                buckets['F (0-59)'] += 1

        max_count = max(buckets.values()) if buckets.values() else 0
        bar_width = 40

        table = create_styled_table(title=f"\n{course_title} Grade Distribution")
        table.add_column("Grade Range")
        table.add_column("Count")
        table.add_column("Distribution")

        for grade_range, count in buckets.items():
            bar_length = int((count / max_count) * bar_width) if max_count > 0 else 0
            bar = "█" * bar_length
            percentage = (count / len(grades)) * 100 if grades else 0
            table.add_row(
                grade_range,
                f"{count} ({percentage:.1f}%)",
                f"[blue]{bar}[/blue]"
            )

        console.print(table)

    except Exception as e:
        console.print(f"[red]Error displaying distribution:[/red] {str(e)}")


@view.command('summary')
@click.option('--semester', help="Filter by semester")
@click.pass_obj
def view_summary(gradebook: GradeBookCLI, semester: str = None):
    """Show summary of all courses and grades."""
    try:
        cursor = gradebook.gradebook.cursor

        # Get courses with assignment counts
        query = """
            SELECT 
                c.course_id,
                c.course_code, 
                c.course_title, 
                c.semester,
                COUNT(DISTINCT a.assignment_id) as assignment_count,
                EXISTS (
                    SELECT 1 
                    FROM categories cat 
                    WHERE cat.course_id = c.course_id
                ) as has_categories
            FROM courses c
            LEFT JOIN assignments a ON c.course_id = a.course_id
        """
        params = []
        if semester:
            query += " WHERE c.semester = ?"
            params.append(semester)

        query += " GROUP BY c.course_id, c.course_title, c.semester ORDER BY c.semester DESC, c.course_title"

        cursor.execute(query, params)
        results = cursor.fetchall()

        if not results:
            console.print("[yellow]No courses found.[/yellow]")
            return

        table = create_styled_table(title="\nCourse Summary")
        table.add_column("Course", style="cyan")
        table.add_column("Title", style="green")
        table.add_column("Semester")
        table.add_column("Assignments", justify="right")
        table.add_column("Overall Grade", justify="right", style="magenta")

        for course_id, code, title, sem, count, has_categories in results:
            # Calculate the weighted grade for each course
            overall_grade = "N/A"

            try:
                if count > 0 and has_categories:  # Only calculate if there are assignments and categories
                    grade = gradebook.gradebook.calculate_course_grade(course_id)
                    if grade is not None:  # Make sure we got a valid grade back
                        overall_grade = f"{grade:.1f}%"
                        # Color code the grade
                        if grade >= 90:
                            overall_grade = f"[green]{overall_grade}[/green]"
                        elif grade >= 80:
                            overall_grade = f"[blue]{overall_grade}[/blue]"
                        elif grade >= 70:
                            overall_grade = f"[yellow]{overall_grade}[/yellow]"
                        else:
                            overall_grade = f"[red]{overall_grade}[/red]"
            except Exception as e:
                console.print(f"[dim red]Warning: Could not calculate grade for {code}: {str(e)}[/dim red]")
                overall_grade = "[red]Error[/red]"

            table.add_row(
                code,
                title,
                sem,
                str(count),
                overall_grade
            )

        console.print(table)

        # Show semester summaries if not filtered
        if not semester:
            cursor.execute("""
                SELECT 
                    c.semester,
                    COUNT(DISTINCT c.course_id) as course_count,
                    COUNT(DISTINCT a.assignment_id) as total_assignments
                FROM courses c
                LEFT JOIN assignments a ON c.course_id = a.course_id
                GROUP BY c.semester
                ORDER BY c.semester DESC
            """)
            semester_stats = cursor.fetchall()

            if len(semester_stats) > 1:  # Only show if there's more than one semester
                table = create_styled_table(title="\nSemester Summaries")
                table.add_column("Semester", style="cyan")
                table.add_column("Courses", justify="right")
                table.add_column("Total Assignments", justify="right")

                for sem, course_count, assignment_count in semester_stats:
                    table.add_row(sem, str(course_count), str(assignment_count))

                console.print("\n", table)

    except Exception as e:
        console.print(f"[red]Error displaying summary:[/red] {str(e)}")


@cli.group()
def move():
    """Move items between categories"""
    pass


@move.command('assignment')
@click.argument('course_code')
@click.argument('assignment_title')
@click.argument('new_category')
@click.pass_obj
def move_assignment(gradebook: GradeBookCLI, course_code: str, assignment_title: str, new_category: str):
    """Move an assignment to a different category.

    Example:
        gradebook move assignment CHM343 "Exam #1" "Final"
    """
    try:
        cursor = gradebook.gradebook.cursor

        # Get course info
        cursor.execute("""
            SELECT c.course_id, a.assignment_id, curr_cat.category_name as current_category,
                   a.earned_points, a.max_points
            FROM courses c
            JOIN assignments a ON c.course_id = a.course_id
            JOIN categories curr_cat ON a.category_id = curr_cat.category_id
            WHERE c.course_code = ? AND a.title = ?
        """, (course_code, assignment_title))

        result = cursor.fetchone()
        if not result:
            console.print(f"[red]Assignment '{assignment_title}' not found in {course_code}[/red]")
            return

        course_id, assignment_id, current_category, earned_points, max_points = result

        # Get new category ID
        cursor.execute("""
            SELECT category_id 
            FROM categories 
            WHERE course_id = ? AND category_name = ?
        """, (course_id, new_category))

        result = cursor.fetchone()
        if not result:
            console.print(f"[red]Category '{new_category}' not found[/red]")

            # Show available categories
            cursor.execute("""
                SELECT category_name, weight 
                FROM categories 
                WHERE course_id = ?
                ORDER BY category_name
            """, (course_id,))

            categories = cursor.fetchall()
            if categories:
                console.print("\nAvailable categories:")
                for name, weight in categories:
                    console.print(f"- {name} ({weight * 100:.1f}%)")
            return

        new_category_id = result[0]

        # Move the assignment
        cursor.execute("""
            UPDATE assignments 
            SET category_id = ? 
            WHERE assignment_id = ?
        """, (new_category_id, assignment_id))

        gradebook.gradebook.conn.commit()

        percentage = (earned_points / max_points) * 100
        console.print(f"[green]Successfully moved assignment:[/green]")
        console.print(f"'{assignment_title}' ({earned_points}/{max_points}, {percentage:.1f}%)")
        console.print(f"From: {current_category}")
        console.print(f"To: {new_category}")

    except Exception as e:
        console.print(f"[red]Error moving assignment:[/red] {str(e)}")


@cli.group()
def edit():
    """Edit existing records"""
    pass


@edit.command('course')
@click.argument('course_code')
@click.option('--new-code', help="New course code")
@click.option('--title', help="New course title")
@click.option('--semester', help="New semester")
@click.option('--credits', type=int, help="New credit hours")
@click.pass_obj
def edit_course(gradebook: GradeBookCLI, course_code: str, new_code: str,
                title: str, semester: str, credits: int):
    """Edit course details.

    Example:
        gradebook edit course CHM343 --title "New Title"
        gradebook edit course CHM343 --credits 4
    """
    try:
        cursor = gradebook.gradebook.cursor

        # Get current course details
        cursor.execute("""
            SELECT course_id, course_code, course_title, semester, credit_hours
            FROM courses 
            WHERE course_code = ?
        """, (course_code,))

        result = cursor.fetchone()
        if not result:
            console.print(f"[red]Course '{course_code}' not found[/red]")
            return

        course_id, curr_code, curr_title, curr_semester, curr_credits = result

        # Build update query based on provided options
        updates = []
        params = []

        if new_code:
            updates.append("course_code = ?")
            params.append(new_code.upper())

        if title:
            updates.append("course_title = ?")
            params.append(title)

        if semester:
            updates.append("semester = ?")
            params.append(semester)

        if credits is not None:
            if credits <= 0:
                console.print("[red]Error: Credit hours must be greater than 0[/red]")
                return
            updates.append("credit_hours = ?")
            params.append(credits)

        if not updates:
            console.print("[yellow]No changes specified. Use --help to see available options.[/yellow]")
            return

        # Add course_id to params
        params.append(course_id)

        # Perform update
        query = f"UPDATE courses SET {', '.join(updates)} WHERE course_id = ?"
        cursor.execute(query, params)
        gradebook.gradebook.conn.commit()

        # Show before/after comparison
        table = Table(title="Course Updated", box=box.ROUNDED)
        table.add_column("Field", style="cyan")
        table.add_column("Old Value", style="yellow")
        table.add_column("New Value", style="green")

        # Get updated course details
        cursor.execute("""
            SELECT course_code, course_title, semester, credit_hours
            FROM courses 
            WHERE course_id = ?
        """, (course_id,))
        new_code, new_title, new_sem, new_credits = cursor.fetchone()

        if new_code != curr_code:
            table.add_row("Course Code", curr_code, new_code)
        if new_title != curr_title:
            table.add_row("Title", curr_title, new_title)
        if new_sem != curr_semester:
            table.add_row("Semester", curr_semester, new_sem)
        if new_credits != curr_credits:
            table.add_row("Credit Hours", str(curr_credits), str(new_credits))

        console.print(table)

    except Exception as e:
        gradebook.gradebook.conn.rollback()
        console.print(f"[red]Error editing course:[/red] {str(e)}")


@edit.command('assignment')
@click.argument('course_code')
@click.argument('assignment_title')
@click.option('--new-title', help="New title for the assignment")
@click.option('--earned', type=float, help="New earned points")
@click.option('--max', type=float, help="New maximum points")
@click.option('--category', help="Move to different category")
@click.pass_obj
def edit_assignment(gradebook: GradeBookCLI, course_code: str, assignment_title: str,
                    new_title: str, earned: float, max: float, category: str):
    """Edit an existing assignment's details.

    Example:
        gradebook edit assignment CHM343 "Exam #1" --earned 45
        gradebook edit assignment CHM343 "Exam #1" --category "Final"
        gradebook edit assignment CHM343 "Exam #1" --new-title "Midterm #1"
    """
    try:
        cursor = gradebook.gradebook.cursor

        # First get the current assignment details
        cursor.execute("""
            SELECT 
                a.assignment_id,
                a.title,
                a.earned_points,
                a.max_points,
                c.category_name,
                c.category_id,
                co.course_id
            FROM assignments a
            JOIN categories c ON a.category_id = c.category_id
            JOIN courses co ON a.course_id = co.course_id
            WHERE co.course_code = ? AND a.title = ?
        """, (course_code, assignment_title))

        result = cursor.fetchone()
        if not result:
            console.print(f"[red]Assignment '{assignment_title}' not found in {course_code}[/red]")
            return

        assignment_id, curr_title, curr_earned, curr_max, curr_category, curr_category_id, course_id = result

        # Build update query based on provided options
        updates = []
        params = []

        if new_title:
            updates.append("title = ?")
            params.append(new_title)

        if earned is not None:
            if earned > (max if max is not None else curr_max):
                console.print("[red]Error: Earned points cannot exceed maximum points[/red]")
                return
            updates.append("earned_points = ?")
            params.append(earned)

        if max is not None:
            if max < (earned if earned is not None else curr_earned):
                console.print("[red]Error: Maximum points cannot be less than earned points[/red]")
                return
            if max <= 0:
                console.print("[red]Error: Maximum points must be greater than 0[/red]")
                return
            updates.append("max_points = ?")
            params.append(max)

        new_category_id = None
        if category:
            # Verify new category exists
            cursor.execute("""
                SELECT category_id 
                FROM categories 
                WHERE course_id = ? AND category_name = ?
            """, (course_id, category))

            result = cursor.fetchone()
            if not result:
                console.print(f"[red]Category '{category}' not found[/red]")
                # Show available categories
                cursor.execute("""
                    SELECT category_name, weight 
                    FROM categories 
                    WHERE course_id = ?
                    ORDER BY category_name
                """, (course_id,))
                categories = cursor.fetchall()
                if categories:
                    console.print("\nAvailable categories:")
                    for name, weight in categories:
                        console.print(f"- {name} ({weight * 100:.1f}%)")
                return

            new_category_id = result[0]
            updates.append("category_id = ?")
            params.append(new_category_id)

        if not updates:
            console.print("[yellow]No changes specified. Use --help to see available options.[/yellow]")
            return

        # Add assignment_id to params
        params.append(assignment_id)

        # Perform update
        cursor.execute(f"""
            UPDATE assignments 
            SET {', '.join(updates)}
            WHERE assignment_id = ?
        """, params)

        gradebook.gradebook.conn.commit()

        # Show updated assignment details
        cursor.execute("""
            SELECT 
                a.title,
                a.earned_points,
                a.max_points,
                c.category_name,
                c.weight
            FROM assignments a
            JOIN categories c ON a.category_id = c.category_id
            WHERE a.assignment_id = ?
        """, (assignment_id,))

        new_title, new_earned, new_max, new_category, category_weight = cursor.fetchone()
        percentage = (new_earned / new_max) * 100
        weighted_score = percentage * category_weight

        # Show success message with before/after comparison
        table = create_styled_table(title="\nAssignment Updated")
        table.add_column("Field", style="cyan")
        table.add_column("Old Value", style="yellow")
        table.add_column("New Value", style="green")

        if new_title != curr_title:
            table.add_row("Title", curr_title, new_title)

        if new_earned != curr_earned:
            table.add_row("Earned Points", str(curr_earned), str(new_earned))

        if new_max != curr_max:
            table.add_row("Maximum Points", str(curr_max), str(new_max))

        if new_category != curr_category:
            table.add_row("Category", curr_category, new_category)

        console.print(table)

        console.print(f"\nUpdated Score: [bold]{new_earned}/{new_max}[/bold] ([green]{percentage:.1f}%[/green])")
        console.print(f"Weighted Score: [magenta]{weighted_score:.1f}%[/magenta]")

        # Show new course grade
        overall_grade = gradebook.gradebook.calculate_course_grade(course_id)
        console.print(f"Updated Course Grade: [bold magenta]{overall_grade:.1f}%[/bold magenta]")

    except Exception as e:
        gradebook.gradebook.conn.rollback()
        console.print(f"[red]Error editing assignment:[/red] {str(e)}")


@edit.command('category')
@click.argument('course_code')
@click.argument('category_name')
@click.option('--new-name', help="New name for the category")
@click.option('--weight', type=float, help="New weight for the category (as decimal)")
@click.pass_obj
def edit_category(gradebook: GradeBookCLI, course_code: str, category_name: str,
                  new_name: str, weight: float):
    """Edit a category's name or weight.

    Example:
        gradebook edit category CHM343 "Homework" --weight 0.35
        gradebook edit category CHM343 "Exams" --new-name "Tests"
    """
    try:
        cursor = gradebook.gradebook.cursor

        # Get category details
        cursor.execute("""
            SELECT c.category_id, c.weight, co.course_id
            FROM categories c
            JOIN courses co ON c.course_id = co.course_id
            WHERE co.course_code = ? AND c.category_name = ?
        """, (course_code, category_name))

        result = cursor.fetchone()
        if not result:
            console.print(f"[red]Category '{category_name}' not found in {course_code}[/red]")
            return

        category_id, curr_weight, course_id = result

        updates = []
        params = []

        if new_name:
            if new_name.lower() == "unallocated":
                console.print("[red]Error: Cannot rename a category to 'Unallocated'[/red]")
                return
            updates.append("category_name = ?")
            params.append(new_name)

        if weight is not None:
            if category_name.lower() == "unallocated":
                console.print("[red]Error: Cannot modify weight of Unallocated category[/red]")
                return

            if weight <= 0:
                console.print("[red]Error: Weight must be greater than 0[/red]")
                return

            weight_difference = curr_weight - weight

            if weight_difference < 0:  # Need more weight
                # Look for Unallocated category
                cursor.execute("""
                    SELECT category_id, weight 
                    FROM categories 
                    WHERE course_id = ? AND LOWER(category_name) = 'unallocated'
                """, (course_id,))
                unallocated = cursor.fetchone()

                if not unallocated:
                    console.print(
                        "[red]Error: Cannot increase weight without an Unallocated category to draw from[/red]")
                    return

                unallocated_id, unallocated_weight = unallocated
                if abs(weight_difference) > unallocated_weight:
                    console.print(
                        f"[red]Error: Not enough weight available in Unallocated category (has {unallocated_weight * 100:.1f}%)[/red]")
                    return

                # Update Unallocated weight
                new_unallocated_weight = unallocated_weight + weight_difference
                if new_unallocated_weight > 0.0001:  # Keep if there's meaningful weight left
                    cursor.execute("""
                        UPDATE categories 
                        SET weight = ?
                        WHERE category_id = ?
                    """, (new_unallocated_weight, unallocated_id))
                else:  # Remove if effectively zero
                    cursor.execute("""
                        DELETE FROM categories 
                        WHERE category_id = ?
                    """, (unallocated_id,))

            elif weight_difference > 0:  # Reducing weight
                # Check if Unallocated category exists
                cursor.execute("""
                    SELECT category_id, weight 
                    FROM categories 
                    WHERE course_id = ? AND LOWER(category_name) = 'unallocated'
                """, (course_id,))
                unallocated = cursor.fetchone()

                if unallocated:
                    # Add to existing Unallocated category
                    unallocated_id, unallocated_weight = unallocated
                    new_unallocated_weight = unallocated_weight + weight_difference
                    cursor.execute("""
                        UPDATE categories 
                        SET weight = ?
                        WHERE category_id = ?
                    """, (new_unallocated_weight, unallocated_id))
                else:
                    # Create new Unallocated category
                    cursor.execute("""
                        INSERT INTO categories (course_id, category_name, weight)
                        VALUES (?, 'Unallocated', ?)
                    """, (course_id, weight_difference))

            updates.append("weight = ?")
            params.append(weight)

        if not updates:
            console.print("[yellow]No changes specified. Use --help to see available options.[/yellow]")
            return

        params.append(category_id)

        cursor.execute(f"""
            UPDATE categories 
            SET {', '.join(updates)}
            WHERE category_id = ?
        """, params)

        gradebook.gradebook.conn.commit()

        # Show updated categories
        cursor.execute("""
            SELECT category_name, weight
            FROM categories
            WHERE course_id = ?
            ORDER BY 
                CASE WHEN LOWER(category_name) = 'unallocated' THEN 1 ELSE 0 END,
                category_name
        """, (course_id,))

        categories = cursor.fetchall()

        table = create_styled_table(title="\nUpdated Category Weights")
        table.add_column("Category", style="cyan")
        table.add_column("Weight", justify="right", style="green")

        total_weight = 0
        for cat_name, cat_weight in categories:
            total_weight += cat_weight
            style = "dim" if cat_name.lower() == "unallocated" else None
            name_cell = f"[{style or ''}]{cat_name}[/{style or ''}]" if style else cat_name
            weight_cell = f"[{style or ''}]{cat_weight * 100:.1f}%[/{style or ''}]" if style else f"{cat_weight * 100:.1f}%"

            if cat_name == (new_name or category_name):  # Highlight changed category
                name_cell = f"[bold green]{cat_name}[/bold green]"
                weight_cell = f"[bold green]{cat_weight * 100:.1f}%[/bold green]"

            table.add_row(name_cell, weight_cell)

        console.print(table)

        # Verify total is 100%
        if abs(total_weight - 1.0) > 0.0001:
            console.print(f"[yellow]Warning: Total weights sum to {total_weight * 100:.1f}%[/yellow]")

    except Exception as e:
        gradebook.gradebook.conn.rollback()
        console.print(f"[red]Error editing category:[/red] {str(e)}")

@cli.group()
def export():
    """Export gradebook data to files"""
    pass


def export_course_to_file(gradebook: GradeBookCLI, course_code: str, output_path: Path, format: str):
    """Internal function to handle course export logic."""
    cursor = gradebook.gradebook.cursor

    # Get course information
    cursor.execute("""
        SELECT c.course_title, c.semester, c.course_id 
        FROM courses c 
        WHERE c.course_code = ?
    """, (course_code,))

    course = cursor.fetchone()
    if not course:
        raise ValueError(f"Course '{course_code}' not found!")

    course_title, semester, course_id = course

    # Get categories and assignments
    cursor.execute("""
        SELECT 
            c.category_name,
            c.weight,
            a.title,
            a.max_points,
            a.earned_points,
            a.entry_date,
            CASE 
                WHEN a.max_points > 0 
                THEN (a.earned_points / a.max_points * c.weight) 
                ELSE 0 
            END as weighted_score
        FROM categories c
        LEFT JOIN assignments a ON c.category_id = a.category_id
        WHERE c.course_id = ?
        ORDER BY c.category_name, COALESCE(a.title, '')
    """, (course_id,))

    results = cursor.fetchall()

    try:
        overall_grade = gradebook.gradebook.calculate_course_grade(course_id)
    except Exception:
        overall_grade = 0.0

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if format == 'txt':
        with open(output_path, 'w') as f:
            # Write header
            f.write(f"{course_code}: {course_title}\n")
            f.write(f"Semester: {semester}\n")
            f.write(f"Overall Grade: {overall_grade:.2f}%\n\n")

            current_category = None
            category_total = 0.0
            category_count = 0

            for cat_name, weight, title, max_points, earned_points, date, weighted_score in results:
                if cat_name != current_category:
                    if current_category and category_count > 0:
                        f.write(f"Category Average: {(category_total / category_count):.2f}%\n\n")

                    f.write(f"{cat_name} ({(weight * 100):.2f}%)\n")
                    f.write("-" * 64 + "\n")
                    current_category = cat_name
                    category_total = 0.0
                    category_count = 0

                if title:  # If there's an assignment
                    percentage = (earned_points / max_points) * 100
                    category_total += percentage
                    category_count += 1
                    date_str = datetime.strptime(date, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d')
                    f.write(f"{title:<30} {earned_points:>5.1f}/{max_points:<5.1f} ")
                    f.write(f"({percentage:>5.1f}%) [{date_str}]\n")

            if category_count > 0:
                f.write(f"Category Average: {(category_total / category_count):.2f}%\n")

    elif format == 'csv':
        with open(output_path, 'w') as f:
            f.write(f"Course,{course_code}\n")
            f.write(f"Title,{course_title}\n")
            f.write(f"Semester,{semester}\n")
            f.write(f"Overall Grade,{overall_grade:.2f}%\n\n")

            f.write("Category,Weight,Assignment,Max Points,Earned Points,Percentage,Date\n")

            for cat_name, weight, title, max_points, earned_points, date, _ in results:
                if title:
                    percentage = (earned_points / max_points) * 100
                    date_str = datetime.strptime(date, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d')
                    f.write(f'"{cat_name}",{weight:.2f},"{title}",')
                    f.write(f"{max_points},{earned_points},{percentage:.1f},{date_str}\n")
                else:
                    f.write(f'"{cat_name}",{weight:.2f},,,,\n')


@export.command('course')
@click.argument('course_code')
@click.option('--output', '-o', help="Output file path (default: <course_code>.txt)")
@click.option('--format', '-f', type=click.Choice(['txt', 'csv']), default='txt',
              help="Output format (default: txt)")
@click.pass_obj
def export_course(gradebook: GradeBookCLI, course_code: str, output: str, format: str):
    """Export a single course's data to a file.

    Example:
        gradebook export course CHM343
        gradebook export course CHM343 --format csv
        gradebook export course CHM343 -o ~/Desktop/chemistry.txt
    """
    try:
        if not output:
            output = f"{course_code}.{format}"
        output_path = Path(output).expanduser()

        export_course_to_file(gradebook, course_code, output_path, format)
        console.print(f"[green]Successfully exported to:[/green] {output_path}")

    except Exception as e:
        console.print(f"[red]Error exporting course:[/red] {str(e)}")


@export.command('all')
@click.option('--output-dir', '-o', default=str(Path('~/.gradebook/exports').expanduser()),
              help="Output directory (default: ~/.gradebook/exports)")
@click.option('--format', '-f', type=click.Choice(['txt', 'csv']), default='txt',
              help="Output format (default: txt)")
@click.pass_obj
def export_all(gradebook: GradeBookCLI, output_dir: str, format: str):
    """Export all courses to individual files.

    Example:
        gradebook export all
        gradebook export all --format csv
        gradebook export all -o ~/Desktop/grades
    """
    try:
        cursor = gradebook.gradebook.cursor

        # Get all courses
        cursor.execute("""
            SELECT course_code, semester
            FROM courses
            ORDER BY semester DESC, course_code
        """)

        courses = cursor.fetchall()
        if not courses:
            console.print("[yellow]No courses found to export[/yellow]")
            return

        # Create output directory
        output_path = Path(output_dir).expanduser()
        output_path.mkdir(parents=True, exist_ok=True)

        success_count = 0
        for course_code, semester in courses:
            try:
                file_path = output_path / f"{course_code}_{semester}.{format}"
                export_course_to_file(gradebook, course_code, file_path, format)
                success_count += 1
                console.print(f"[green]Exported {course_code}[/green]")
            except Exception as e:
                console.print(f"[red]Error exporting {course_code}:[/red] {str(e)}")

        console.print(
            f"\n[green]Successfully exported {success_count} of {len(courses)} courses to:[/green] {output_path}")

    except Exception as e:
        console.print(f"[red]Error exporting courses:[/red] {str(e)}")


def main() -> None:
    cli_obj = None
    try:
        cli_obj = GradeBookCLI()
        cli()
    except Exception as e:
        console.print(f"[red]Fatal error:[/red] {str(e)}")
    finally:
        if cli_obj is not None:
            cli_obj.close()

if __name__ == '__main__':
    main()
