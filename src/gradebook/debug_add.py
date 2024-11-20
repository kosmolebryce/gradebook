import sqlite3
from pathlib import Path
from rich.console import Console
from rich.table import Table

console = Console()


def debug_add_course(course_code: str, course_title: str, semester: str):
    """Debug the course addition process step by step."""
    db_path = Path("~/.gradebook/gradebook.db").expanduser()
    console.print(f"\n[cyan]Starting debugging session for adding course: {course_code}[/cyan]")

    try:
        # 1. Check database existence and connection
        console.print("\n[yellow]1. Database Connection Check:[/yellow]")
        console.print(f"Database path: {db_path}")
        console.print(f"Database exists: {db_path.exists()}")
        console.print(f"Database is file: {db_path.is_file() if db_path.exists() else 'N/A'}")

        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        console.print("[green]Successfully connected to database[/green]")

        # 2. Check tables existence
        console.print("\n[yellow]2. Table Structure Check:[/yellow]")
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        for table in tables:
            console.print(f"Found table: {table[0]}")
            cursor.execute(f"PRAGMA table_info({table[0]})")
            columns = cursor.fetchall()
            for col in columns:
                console.print(f"  - Column: {col[1]} ({col[2]})")

        # 3. Attempt course addition
        console.print("\n[yellow]3. Adding Course:[/yellow]")
        console.print(f"Attempting to add: {course_code} - {course_title} ({semester})")

        cursor.execute("""
            INSERT INTO courses (course_code, course_title, semester)
            VALUES (?, ?, ?)
        """, (course_code.upper(), course_title, semester))
        course_id = cursor.lastrowid
        conn.commit()

        console.print(f"[green]Course added with ID: {course_id}[/green]")

        # 4. Verify addition
        console.print("\n[yellow]4. Verifying Course Addition:[/yellow]")
        cursor.execute("""
            SELECT course_id, course_code, course_title, semester 
            FROM courses 
            WHERE course_id = ?
        """, (course_id,))
        course = cursor.fetchone()

        if course:
            table = Table(title="Added Course Details")
            table.add_column("Field", style="cyan")
            table.add_column("Value", style="green")

            table.add_row("ID", str(course[0]))
            table.add_row("Code", course[1])
            table.add_row("Title", course[2])
            table.add_row("Semester", course[3])

            console.print(table)
        else:
            console.print("[red]Failed to verify course addition![/red]")

        # 5. Show all courses
        console.print("\n[yellow]5. All Courses in Database:[/yellow]")
        cursor.execute("SELECT course_id, course_code, course_title, semester FROM courses")
        courses = cursor.fetchall()

        if courses:
            table = Table(title="All Courses")
            table.add_column("ID", style="dim")
            table.add_column("Code", style="cyan")
            table.add_column("Title", style="green")
            table.add_column("Semester", style="yellow")

            for c in courses:
                table.add_row(str(c[0]), c[1], c[2], c[3])

            console.print(table)
        else:
            console.print("[red]No courses found in database![/red]")

    except Exception as e:
        console.print(f"[red]Error during debugging:[/red] {str(e)}")
        raise
    finally:
        if 'conn' in locals():
            conn.close()


if __name__ == "__main__":
    # Test adding a course
    debug_add_course("CHM343", "Organic Chemistry II", "FA24")