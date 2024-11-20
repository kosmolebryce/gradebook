import click
import importlib.metadata
import statistics
import sys
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich import box
from datetime import datetime
from typing import List, Tuple
from rich.layout import Layout
from rich.live import Live
from rich.progress import Progress, SpinnerColumn
from rich import print as rprint
from datetime import datetime, timedelta
from pathlib import Path

from gradebook.db import Gradebook

console = Console()

DB_NAME = Path("~/.gradebook/gradebook.db").expanduser()
if not DB_NAME.exists():
    DB_NAME.mkdir(parents=True, exist_ok=True)

def format_percentage(value: float) -> str:
    """Format a decimal to percentage with 2 decimal places."""
    return f"{value * 100:.2f}%"

class GradeBookCLI:
    def __init__(self, db_name=DB_NAME):
        self.gradebook = Gradebook(db_name)

    def close(self):
        self.gradebook.close()

def get_version():
    """Get version from Poetry's package metadata."""
    try:
        return importlib.metadata.version("gradebook")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"

@click.group()
@click.version_option(version=get_version(), prog_name="gradebook")
@click.pass_context
def cli(ctx):
    """Gradebook Management System"""
    ctx.obj = GradeBookCLI()

@cli.group()
def add():
    """Add items to the gradebook"""
    pass


@add.command('course')
@click.argument('course_code')
@click.argument('course_title')
@click.argument('semester')
@click.pass_obj
def add_course(gradebook: GradeBookCLI, course_code: str, course_title: str, semester: str):
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

        # Add the course - simplified without RETURNING clause
        cursor.execute("""
            INSERT INTO courses (course_code, course_title, semester) 
            VALUES (?, ?, ?)
        """, (course_code, course_title, semester))

        gradebook.gradebook.conn.commit()

        # Verify the course was added by selecting it
        cursor.execute("""
            SELECT course_id FROM courses 
            WHERE course_code = ? AND semester = ?
        """, (course_code, semester))

        result = cursor.fetchone()
        if result:
            course_id = result[0]
            console.print(f"[green]Successfully added course:[/green] {course_code}: {course_title} ({semester})")
            console.print(f"[dim]Debug: Added course with ID {course_id}[/dim]")
            console.print(f"Now add categories with: gradebook add categories {course_code}")
        else:
            console.print("[red]Failed to verify course was added![/red]")

    except Exception as e:
        gradebook.gradebook.conn.rollback()  # Rollback on error
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

            table = Table(title="Current Categories")
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

                table = Table(title="New Categories", box=box.ROUNDED)
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
    """Add a single category to a course (weight must come from Unallocated).

    Example:
        gradebook add category CHM343 "Quizzes" 0.10
    """
    try:
        cursor = gradebook.gradebook.cursor
        course_id = gradebook.gradebook.get_course_id_by_code(course_code, semester)

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
        if weight > unallocated_weight:
            console.print(f"[red]Error: Not enough weight in Unallocated category "
                          f"(has {unallocated_weight * 100:.1f}%, needs {weight * 100:.1f}%)[/red]")
            return

        # Update Unallocated weight
        new_unallocated = unallocated_weight - weight
        if new_unallocated > 0.0001:  # Keep if there's meaningful weight left
            cursor.execute("""
                UPDATE categories 
                SET weight = ?
                WHERE category_id = ?
            """, (new_unallocated, unallocated_id))
        else:  # Remove if effectively zero
            cursor.execute("""
                DELETE FROM categories 
                WHERE category_id = ?
            """, (unallocated_id,))

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

        table = Table(title=f"Categories for {course_code}", box=box.ROUNDED)
        table.add_column("Category", style="cyan")
        table.add_column("Weight", justify="right", style="green")

        for cat_name, cat_weight in categories:
            style = "dim" if cat_name.lower() == "unallocated" else None
            name_cell = f"[{style or ''}]{cat_name}[/{style or ''}]" if style else cat_name
            weight_cell = f"[{style or ''}]{cat_weight * 100:.1f}%[/{style or ''}]" if style else f"{cat_weight * 100:.1f}%"

            if cat_name == category_name:  # Highlight new category
                name_cell = f"[bold green]{cat_name}[/bold green]"
                weight_cell = f"[bold green]{cat_weight * 100:.1f}%[/bold green]"

            table.add_row(name_cell, weight_cell)

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
    """
    try:
        cursor = gradebook.gradebook.cursor

        # Get course ID
        cursor.execute("""
            SELECT course_id, semester 
            FROM courses 
            WHERE course_code = ?
        """, (course_code,))

        courses = cursor.fetchall()
        if not courses:
            console.print(f"[red]Error:[/red] Course '{course_code}' not found")
            return
        elif len(courses) > 1:
            semesters = [c[1] for c in courses]
            console.print(
                f"[yellow]Multiple sections found for {course_code}. Available semesters: {', '.join(semesters)}")
            semester = Prompt.ask("Please specify semester")
            cursor.execute("""
                SELECT course_id 
                FROM courses 
                WHERE course_code = ? AND semester = ?
            """, (course_code, semester))
            result = cursor.fetchone()
            if not result:
                console.print(f"[red]Error:[/red] Course '{course_code}' not found for semester '{semester}'")
                return
            course_id = result[0]
        else:
            course_id = courses[0][0]

        # Get category ID
        cursor.execute("""
            SELECT category_id 
            FROM categories 
            WHERE course_id = ? AND category_name = ?
        """, (course_id, category_name))

        category = cursor.fetchone()
        if not category:
            console.print(f"[red]Error:[/red] Category '{category_name}' not found")
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

        category_id = category[0]

        # Validate points
        if earned_points > max_points:
            console.print(f"[red]Error:[/red] Earned points ({earned_points}) cannot exceed max points ({max_points})")
            return

        if max_points <= 0:
            console.print(f"[red]Error:[/red] Max points must be greater than 0")
            return

        # Add the assignment
        assignment_id = gradebook.gradebook.add_assignment(
            course_id, category_id, title, max_points, earned_points
        )

        percentage = (earned_points / max_points) * 100

        # Create success message with detailed information
        console.print(Panel(f"""[green]Successfully added assignment![/green]
Course: {course_code}
Category: {category_name}
Title: {title}
Score: {earned_points}/{max_points} ({percentage:.2f}%)""",
                            title="New Assignment",
                            border_style="green"
                            ))

        # Show updated course grade
        overall_grade = gradebook.gradebook.calculate_course_grade(course_id)
        console.print(f"\nUpdated course grade: [bold magenta]{overall_grade:.2f}%[/bold magenta]")

    except Exception as e:
        console.print(f"[red]Error adding assignment:[/red] {str(e)}")

@cli.group()
def show():
    """Display detailed information"""
    pass


@show.command('course')
@click.argument('course_code')
@click.pass_obj
def show_course(gradebook: GradeBookCLI, course_code: str):
    """Display all information for a course."""
    try:
        cursor = gradebook.gradebook.cursor

        # Get course information
        cursor.execute("""
            SELECT c.course_title, c.semester, c.course_id 
            FROM courses c 
            WHERE c.course_code = ?
        """, (course_code,))

        course = cursor.fetchone()
        if not course:
            console.print("[red]Course not found![/red]")
            return

        course_title, semester, course_id = course

        # Get all categories and assignments
        cursor.execute("""
            SELECT 
                c.category_name,
                c.weight,
                a.title,
                a.max_points,
                a.earned_points,
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

        if not results:
            console.print("[yellow]No categories found for this course.[/yellow]")
            return

        table = Table(title=f"{course_title} - {semester}", box=box.ROUNDED)
        table.add_column("Category", style="cyan")
        table.add_column("Assignment", style="green")
        table.add_column("Score", justify="right")
        table.add_column("Weight", justify="right")
        table.add_column("Weighted Score", justify="right", style="magenta")

        current_category = None
        category_scores = {}

        for category, weight, title, max_points, earned_points, weighted_score in results:
            if category != current_category:
                # Print category row
                table.add_row(
                    f"[bold]{category}[/bold]",
                    "",
                    "",
                    f"[bold]{weight * 100:.1f}%[/bold]",
                    ""
                )
                current_category = category

            if title:  # If there's an assignment
                percentage = (earned_points / max_points) * 100
                table.add_row(
                    "",
                    title,
                    f"{earned_points}/{max_points} ({percentage:.1f}%)",
                    "",
                    f"{weighted_score * 100:.1f}%"
                )

                if category not in category_scores:
                    category_scores[category] = []
                category_scores[category].append(weighted_score)

        console.print(table)

        # Calculate and show overall grade
        try:
            overall_grade = gradebook.gradebook.calculate_course_grade(course_id)
            console.print(f"\nOverall Grade: [bold magenta]{overall_grade:.1f}%[/bold magenta]")

            # Show category averages
            console.print("\nCategory Averages:")
            for category, scores in category_scores.items():
                if scores:
                    avg = sum(scores) / len(scores) * 100
                    console.print(f"{category}: [cyan]{avg:.1f}%[/cyan]")

        except Exception as e:
            console.print(f"[yellow]Note: {str(e)}[/yellow]")

    except Exception as e:
        console.print(f"[red]Error displaying course:[/red] {str(e)}")

@cli.group()
def list():
    """List items in the gradebook"""
    pass

@list.command('courses')
@click.pass_obj
def list_courses(gradebook: GradeBookCLI):
    """List all courses in the gradebook."""
    try:
        cursor = gradebook.gradebook.cursor
        cursor.execute("""
            SELECT c.course_code, c.course_title, c.semester,
                   COUNT(DISTINCT a.assignment_id) as assignment_count
            FROM courses c
            LEFT JOIN assignments a ON c.course_id = a.course_id
            GROUP BY c.course_id
            ORDER BY c.semester DESC, c.course_title
        """)
        courses = cursor.fetchall()

        if not courses:
            console.print("[yellow]No courses found in gradebook.[/yellow]")
            return

        table = Table(title="All Courses", box=box.ROUNDED)
        table.add_column("Code", style="cyan")
        table.add_column("Course", style="green")
        table.add_column("Semester")
        table.add_column("Assignments", justify="right")
        table.add_column("Overall Grade", justify="right")

        for code, name, semester, assignment_count in courses:
            grade = gradebook.gradebook.calculate_course_grade(code) if assignment_count > 0 else "N/A"
            grade_str = f"{grade}%" if grade != "N/A" else grade
            table.add_row(
                code,
                name,
                semester,
                str(assignment_count),
                grade_str
            )

        console.print(table)

    except Exception as e:
        console.print(f"[red]Error listing courses:[/red] {str(e)}")

@list.command('categories')
@click.argument('course_code')
@click.pass_obj
def list_categories(gradebook: GradeBookCLI, course_code: str):
    """List all categories for a course."""
    try:
        course_id = gradebook.gradebook.get_course_id_by_code(course_code)
        cursor = gradebook.gradebook.cursor
        cursor.execute("""
            SELECT cat.category_id, cat.category_name, cat.weight,
                   COUNT(a.assignment_id) as assignment_count,
                   COALESCE(AVG(a.earned_points / a.max_points), 0) as avg_score
            FROM categories cat
            LEFT JOIN assignments a ON cat.category_id = a.category_id
            WHERE cat.course_id = ?
            GROUP BY cat.category_id
        """, (course_id,))
        categories = cursor.fetchall()

        if not categories:
            console.print("[yellow]No categories found for this course.[/yellow]")
            return

        table = Table(title="Course Categories", box=box.ROUNDED)
        table.add_column("ID", justify="right", style="cyan")
        table.add_column("Category", style="green")
        table.add_column("Weight", justify="right")
        table.add_column("Assignments", justify="right")
        table.add_column("Average Score", justify="right")

        for cat_id, name, weight, assignment_count, avg_score in categories:
            table.add_row(
                str(cat_id),
                name,
                format_percentage(weight),
                str(assignment_count),
                format_percentage(avg_score) if assignment_count > 0 else "N/A"
            )

        console.print(table)

    except Exception as e:
        console.print(f"[red]Error listing categories:[/red] {str(e)}")

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
def remove_assignment(gradebook: GradeBookCLI, course_code: str, assignment_title: str,
                      semester: str, force: bool):
    """Remove an assignment by name."""
    try:
        course_id = gradebook.gradebook.get_course_id_by_code(course_code, semester)
        assignment_id = gradebook.gradebook.get_assignment_id(course_id, assignment_title)

        cursor = gradebook.gradebook.cursor
        cursor.execute("""
            SELECT a.earned_points, a.max_points, cat.category_name
            FROM assignments a
            JOIN categories cat ON a.category_id = cat.category_id
            WHERE a.assignment_id = ?
        """, (assignment_id,))

        earned_points, max_points, category_name = cursor.fetchone()

        if not force:
            console.print(f"[yellow]Warning: This will remove the assignment '[bold]{assignment_title}[/bold]' "
                          f"({earned_points}/{max_points}) from {category_name} in {course_code}![/yellow]")
            if not Confirm.ask("Are you sure you want to proceed?"):
                console.print("Operation cancelled.")
                return

        cursor.execute("DELETE FROM assignments WHERE assignment_id = ?", (assignment_id,))
        gradebook.gradebook.conn.commit()

        console.print(f"[green]Successfully removed assignment: {assignment_title}[/green]")

    except Exception as e:
        console.print(f"[red]Error removing assignment:[/red] {str(e)}")

@cli.group()
def view():
    """Visualize gradebook data"""
    pass

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

        table = Table(title=f"{course_title} Grade Distribution")
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

        # Main summary query (unchanged from before)
        query = """
            SELECT 
                c.course_code, 
                c.course_title, 
                c.semester,
                COUNT(DISTINCT a.assignment_id) as assignment_count,
                COALESCE(AVG(CASE 
                    WHEN a.max_points > 0 
                    THEN (a.earned_points / a.max_points * 100) 
                    ELSE NULL 
                END), 0) as avg_grade,
                MIN(CASE 
                    WHEN a.max_points > 0 
                    THEN (a.earned_points / a.max_points * 100) 
                    ELSE NULL 
                END) as min_grade,
                MAX(CASE 
                    WHEN a.max_points > 0 
                    THEN (a.earned_points / a.max_points * 100) 
                    ELSE NULL 
                END) as max_grade
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

        table = Table(title="Course Summary", box=box.ROUNDED)
        table.add_column("Course", style="cyan")
        table.add_column("Title", style="green")
        table.add_column("Semester")
        table.add_column("Assignments", justify="right")
        table.add_column("Average", justify="right", style="magenta")
        table.add_column("Range", justify="right", style="yellow")

        for course, title, sem, count, avg, min_grade, max_grade in results:
            if count > 0:
                grade_range = f"{min_grade:.1f}% - {max_grade:.1f}%" if min_grade is not None else "N/A"
                avg_str = f"{avg:.1f}%"

                # Color-code the average grade
                if avg >= 90:
                    avg_str = f"[green]{avg_str}[/green]"
                elif avg >= 80:
                    avg_str = f"[blue]{avg_str}[/blue]"
                elif avg >= 70:
                    avg_str = f"[yellow]{avg_str}[/yellow]"
                else:
                    avg_str = f"[red]{avg_str}[/red]"
            else:
                grade_range = "N/A"
                avg_str = "N/A"

            table.add_row(
                course,
                title,
                sem,
                str(count),
                avg_str,
                grade_range
            )

        console.print(table)

        # Fixed semester summary query with explicit column references
        if not semester:
            cursor.execute("""
                SELECT 
                    c.semester,
                    COUNT(DISTINCT c.course_id) as course_count,
                    COALESCE(AVG(CASE 
                        WHEN a.max_points > 0 
                        THEN (a.earned_points / a.max_points * 100) 
                        ELSE NULL 
                    END), 0) as semester_avg
                FROM courses c
                LEFT JOIN assignments a ON c.course_id = a.course_id
                GROUP BY c.semester
                ORDER BY c.semester DESC
            """)
            semester_stats = cursor.fetchall()

            if len(semester_stats) > 1:  # Only show if there's more than one semester
                table = Table(title="Semester Summaries", box=box.ROUNDED)
                table.add_column("Semester", style="cyan")
                table.add_column("Courses", justify="right")
                table.add_column("Average", justify="right", style="magenta")

                for sem, course_count, sem_avg in semester_stats:
                    avg_str = f"{sem_avg:.1f}%" if sem_avg > 0 else "N/A"
                    table.add_row(sem, str(course_count), avg_str)

                console.print("\n", table)

    except Exception as e:
        console.print(f"[red]Error displaying summary:[/red] {str(e)}")
        raise  # For debugging - remove in production

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
        table = Table(title="Assignment Updated", box=box.ROUNDED)
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

        table = Table(title="Updated Category Weights", box=box.ROUNDED)
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

                    f.write(f"{cat_name} ({weight:.2f}%)\n")
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