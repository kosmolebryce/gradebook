import click
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

from gradebook.db import Gradebook

console = Console()


def format_percentage(value: float) -> str:
    """Format a decimal to percentage with 2 decimal places."""
    return f"{value * 100:.2f}%"


class GradeBookCLI:
    def __init__(self, db_name='gradebook.db'):
        self.gradebook = Gradebook(db_name)

    def close(self):
        self.gradebook.close()


@click.group()
@click.pass_context
def cli(ctx):
    """Gradebook Management System"""
    ctx.obj = GradeBookCLI()


@cli.group()
def add():
    """Add items to the gradebook"""
    pass


@add.command('course')
@click.argument('name')
@click.argument('semester')
@click.pass_obj
def add_course(gradebook: GradeBookCLI, name: str, semester: str):
    """Add a new course to the gradebook."""
    try:
        course_id = gradebook.gradebook.add_course(name, semester)
        console.print(f"[green]Successfully added course:[/green] {name} ({semester})")
        console.print("Now add categories with: gradebook add categories <course_id>")
    except Exception as e:
        console.print(f"[red]Error adding course:[/red] {str(e)}")


@add.command('categories')
@click.argument('course_id', type=int)
@click.pass_obj
def add_categories(gradebook: GradeBookCLI, course_id: int):
    """Add categories and weights to a course interactively."""
    try:
        categories = []
        total_weight = 0.0

        while total_weight < 1.0:
            remaining = 1.0 - total_weight
            console.print(f"\nRemaining weight available: [cyan]{format_percentage(remaining)}[/cyan]")

            name = Prompt.ask("Enter category name (or 'done' if finished)")
            if name.lower() == 'done':
                if total_weight < 1.0:
                    console.print("[yellow]Warning: Total weights do not sum to 100%[/yellow]")
                    if not Confirm.ask("Continue anyway?"):
                        continue
                break

            weight = float(Prompt.ask("Enter weight (as decimal)", default="0.25"))
            if weight > remaining:
                console.print("[red]Error: Weight would exceed 100%[/red]")
                continue

            categories.append((name, weight))
            total_weight += weight

        if categories:
            gradebook.gradebook.add_categories(course_id, categories)
            console.print("[green]Successfully added categories![/green]")

            # Display the categories in a table
            table = Table(title=f"Course Categories", box=box.ROUNDED)
            table.add_column("Category", style="cyan")
            table.add_column("Weight", justify="right", style="magenta")

            for name, weight in categories:
                table.add_row(name, format_percentage(weight))

            console.print(table)

    except Exception as e:
        console.print(f"[red]Error adding categories:[/red] {str(e)}")


@add.command('assignment')
@click.argument('course_id', type=int)
@click.argument('category_id', type=int)
@click.argument('title')
@click.argument('max_points', type=float)
@click.argument('earned_points', type=float)
@click.pass_obj
def add_assignment(gradebook: GradeBookCLI, course_id: int, category_id: int,
                   title: str, max_points: float, earned_points: float):
    """Add a new assignment to a course category."""
    try:
        assignment_id = gradebook.gradebook.add_assignment(
            course_id, category_id, title, max_points, earned_points
        )
        percentage = (earned_points / max_points) * 100

        panel = Panel(
            f"""[green]Successfully added assignment![/green]
Title: {title}
Score: {earned_points}/{max_points} ({percentage:.2f}%)""",
            title="New Assignment",
            box=box.ROUNDED
        )
        console.print(panel)

    except Exception as e:
        console.print(f"[red]Error adding assignment:[/red] {str(e)}")


@cli.group()
def show():
    """Display detailed information"""
    pass


@show.command('course')
@click.argument('course_id', type=int)
@click.pass_obj
def show_course(gradebook: GradeBookCLI, course_id: int):
    """Display all information for a course."""
    try:
        # Get course details
        cursor = gradebook.gradebook.cursor
        cursor.execute("SELECT course_name, semester FROM courses WHERE course_id = ?", (course_id,))
        course = cursor.fetchone()
        if not course:
            console.print("[red]Course not found![/red]")
            return

        course_name, semester = course

        # Create main table for course info
        table = Table(title=f"{course_name} - {semester}", box=box.ROUNDED)
        table.add_column("Category", style="cyan")
        table.add_column("Assignment", style="green")
        table.add_column("Score", justify="right")
        table.add_column("Weight", justify="right")
        table.add_column("Weighted Score", justify="right", style="magenta")

        assignments = gradebook.gradebook.get_course_assignments(course_id)
        for title, max_points, earned_points, date, category, weight, weighted_points in assignments:
            percentage = (earned_points / max_points) * 100
            table.add_row(
                category,
                title,
                f"{earned_points}/{max_points} ({percentage:.1f}%)",
                format_percentage(weight),
                format_percentage(weighted_points)
            )

        console.print(table)

        # Show overall grade
        overall_grade = gradebook.gradebook.calculate_course_grade(course_id)
        console.print(f"\nOverall Grade: [bold magenta]{overall_grade}%[/bold magenta]")

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
            SELECT c.course_id, c.course_name, c.semester,
                   COUNT(DISTINCT a.assignment_id) as assignment_count
            FROM courses c
            LEFT JOIN assignments a ON c.course_id = a.course_id
            GROUP BY c.course_id
            ORDER BY c.semester DESC, c.course_name
        """)
        courses = cursor.fetchall()

        if not courses:
            console.print("[yellow]No courses found in gradebook.[/yellow]")
            return

        table = Table(title="All Courses", box=box.ROUNDED)
        table.add_column("ID", justify="right", style="cyan")
        table.add_column("Course", style="green")
        table.add_column("Semester")
        table.add_column("Assignments", justify="right")
        table.add_column("Overall Grade", justify="right")

        for course_id, name, semester, assignment_count in courses:
            grade = gradebook.gradebook.calculate_course_grade(course_id) if assignment_count > 0 else "N/A"
            grade_str = f"{grade}%" if grade != "N/A" else grade
            table.add_row(
                str(course_id),
                name,
                semester,
                str(assignment_count),
                grade_str
            )

        console.print(table)

    except Exception as e:
        console.print(f"[red]Error listing courses:[/red] {str(e)}")


@list.command('categories')
@click.argument('course_id', type=int)
@click.pass_obj
def list_categories(gradebook: GradeBookCLI, course_id: int):
    """List all categories for a course."""
    try:
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


# [Previous imports and class definitions remain the same]

@cli.group()
def remove():
    """Remove items from the gradebook"""
    pass


@remove.command('course')
@click.argument('course_id', type=int)
@click.option('--force', is_flag=True, help="Skip confirmation prompt")
@click.pass_obj
def remove_course(gradebook: GradeBookCLI, course_id: int, force: bool):
    """Remove a course and all its associated data."""
    try:
        # Get course details first
        cursor = gradebook.gradebook.cursor
        cursor.execute("SELECT course_name, semester FROM courses WHERE course_id = ?", (course_id,))
        course = cursor.fetchone()

        if not course:
            console.print("[red]Course not found![/red]")
            return

        course_name, semester = course

        # Show warning and get confirmation
        if not force:
            console.print(f"[yellow]Warning: This will remove the course '[bold]{course_name}[/bold]' ({semester}) "
                          f"and all its categories and assignments![/yellow]")
            if not Confirm.ask("Are you sure you want to proceed?"):
                console.print("Operation cancelled.")
                return

        # Remove the course (cascading delete should handle categories and assignments)
        cursor.execute("""
        DELETE FROM courses WHERE course_id = ?
        """, (course_id,))
        gradebook.gradebook.conn.commit()

        console.print(f"[green]Successfully removed course: {course_name} ({semester})[/green]")

    except Exception as e:
        console.print(f"[red]Error removing course:[/red] {str(e)}")


@remove.command('category')
@click.argument('category_id', type=int)
@click.option('--force', is_flag=True, help="Skip confirmation prompt")
@click.pass_obj
def remove_category(gradebook: GradeBookCLI, category_id: int, force: bool):
    """Remove a category and all its assignments."""
    try:
        # Get category details first
        cursor = gradebook.gradebook.cursor
        cursor.execute("""
            SELECT cat.category_name, c.course_name, COUNT(a.assignment_id) as assignment_count
            FROM categories cat
            JOIN courses c ON cat.course_id = c.course_id
            LEFT JOIN assignments a ON cat.category_id = a.category_id
            WHERE cat.category_id = ?
            GROUP BY cat.category_id
        """, (category_id,))
        result = cursor.fetchone()

        if not result:
            console.print("[red]Category not found![/red]")
            return

        category_name, course_name, assignment_count = result

        # Show warning and get confirmation
        if not force:
            console.print(f"[yellow]Warning: This will remove the category '[bold]{category_name}[/bold]' "
                          f"from course '{course_name}' and its {assignment_count} assignment(s)![/yellow]")
            if not Confirm.ask("Are you sure you want to proceed?"):
                console.print("Operation cancelled.")
                return

        # Remove the category (cascading delete should handle assignments)
        cursor.execute("""
        DELETE FROM categories WHERE category_id = ?
        """, (category_id,))
        gradebook.gradebook.conn.commit()

        console.print(f"[green]Successfully removed category: {category_name}[/green]")

    except Exception as e:
        console.print(f"[red]Error removing category:[/red] {str(e)}")


@remove.command('assignment')
@click.argument('assignment_id', type=int)
@click.option('--force', is_flag=True, help="Skip confirmation prompt")
@click.pass_obj
def remove_assignment(gradebook: GradeBookCLI, assignment_id: int, force: bool):
    """Remove an assignment."""
    try:
        # Get assignment details first
        cursor = gradebook.gradebook.cursor
        cursor.execute("""
            SELECT a.title, a.earned_points, a.max_points, 
                   cat.category_name, c.course_name
            FROM assignments a
            JOIN categories cat ON a.category_id = cat.category_id
            JOIN courses c ON a.course_id = c.course_id
            WHERE a.assignment_id = ?
        """, (assignment_id,))
        result = cursor.fetchone()

        if not result:
            console.print("[red]Assignment not found![/red]")
            return

        title, earned_points, max_points, category_name, course_name = result

        # Show warning and get confirmation
        if not force:
            console.print(f"[yellow]Warning: This will remove the assignment '[bold]{title}[/bold]' "
                          f"({earned_points}/{max_points}) from {category_name} in {course_name}![/yellow]")
            if not Confirm.ask("Are you sure you want to proceed?"):
                console.print("Operation cancelled.")
                return

        # Remove the assignment
        cursor.execute("""
        DELETE FROM assignments WHERE assignment_id = ?
        """, (assignment_id,))
        gradebook.gradebook.conn.commit()

        console.print(f"[green]Successfully removed assignment: {title}[/green]")

    except Exception as e:
        console.print(f"[red]Error removing assignment:[/red] {str(e)}")


@cli.group()
def view():
    """Visualize gradebook data"""
    pass


@view.command('trends')
@click.argument('course_id', type=int)
@click.option('--days', default=30, help="Number of days to analyze")
@click.pass_obj
def view_trends(gradebook: GradeBookCLI, course_id: int, days: int):
    """Show grade trends over time for a course."""
    try:
        cursor = gradebook.gradebook.cursor

        # Get course name
        cursor.execute("SELECT course_name FROM courses WHERE course_id = ?", (course_id,))
        course_name = cursor.fetchone()[0]

        # Get assignments ordered by date
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

        # Calculate running weighted average over time
        dates = []
        grades = []
        running_grade = 0

        for title, earned, max_points, date, category, weight in assignments:
            score = (earned / max_points) * 100
            dates.append(date)
            grades.append(score)
            running_grade = statistics.mean(grades)

        # Create a visualization using rich
        layout = Layout()
        layout.split_column(
            Layout(name="title"),
            Layout(name="graph"),
            Layout(name="stats")
        )

        # Title
        layout["title"].update(Panel(
            f"[bold blue]{course_name}[/bold blue] Grade Trends",
            style="white on blue"
        ))

        # Create ASCII graph
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

        # Statistics
        stats = f"""[green]Latest Grade:[/green] {grades[-1]:.1f}%
[cyan]Average Grade:[/cyan] {statistics.mean(grades):.1f}%
[magenta]Highest Grade:[/magenta] {max(grades):.1f}%
[yellow]Lowest Grade:[/yellow] {min(grades):.1f}%
[blue]Number of Assignments:[/blue] {len(grades)}"""

        layout["stats"].update(Panel(stats, title="Statistics"))

        # Display the complete visualization
        console.print(layout)

    except Exception as e:
        console.print(f"[red]Error displaying trends:[/red] {str(e)}")


@view.command('distribution')
@click.argument('course_id', type=int)
@click.pass_obj
def view_distribution(gradebook: GradeBookCLI, course_id: int):
    """Show grade distribution for a course."""
    try:
        cursor = gradebook.gradebook.cursor

        # Get course name
        cursor.execute("SELECT course_name FROM courses WHERE course_id = ?", (course_id,))
        course_name = cursor.fetchone()[0]

        # Get all grades
        cursor.execute("""
            SELECT (a.earned_points / a.max_points * 100) as percentage
            FROM assignments a
            WHERE a.course_id = ?
        """, (course_id,))
        grades = [row[0] for row in cursor.fetchall()]

        if not grades:
            console.print("[yellow]No grades found for this course.[/yellow]")
            return

        # Create grade distribution buckets
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

        # Create visualization
        max_count = max(buckets.values()) if buckets.values() else 0
        bar_width = 40

        table = Table(title=f"{course_name} Grade Distribution")
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

        # Build query based on whether semester is specified
        query = """
            SELECT c.course_name, c.semester,
                   COUNT(DISTINCT a.assignment_id) as assignment_count,
                   AVG(a.earned_points / a.max_points * 100) as avg_grade,
                   MIN(a.earned_points / a.max_points * 100) as min_grade,
                   MAX(a.earned_points / a.max_points * 100) as max_grade
            FROM courses c
            LEFT JOIN assignments a ON c.course_id = a.course_id
        """
        params = []
        if semester:
            query += " WHERE c.semester = ?"
            params.append(semester)

        query += " GROUP BY c.course_id, c.course_name, c.semester"

        cursor.execute(query, params)
        results = cursor.fetchall()

        if not results:
            console.print("[yellow]No courses found.[/yellow]")
            return

        # Create summary table
        table = Table(title="Course Summary")
        table.add_column("Course")
        table.add_column("Semester")
        table.add_column("Assignments")
        table.add_column("Average", justify="right")
        table.add_column("Range", justify="right")

        for course, sem, count, avg, min_grade, max_grade in results:
            if count > 0:
                grade_range = f"{min_grade:.1f}% - {max_grade:.1f}%"
                avg_str = f"{avg:.1f}%"

                # Color-code average grade
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
                sem,
                str(count),
                avg_str,
                grade_range
            )

        console.print(table)

    except Exception as e:
        console.print(f"[red]Error displaying summary:[/red] {str(e)}")

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