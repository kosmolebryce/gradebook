import sqlite3
from pathlib import Path
from rich.console import Console

console = Console()


def inspect_database(db_path: Path):
    """Inspect the gradebook database contents and structure."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check if database file exists
        console.print(f"\n[cyan]Database Path Check:[/cyan]")
        console.print(f"Path exists: {db_path.exists()}")
        console.print(f"Full path: {db_path.absolute()}")

        # List all tables
        console.print("\n[cyan]Database Tables:[/cyan]")
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        if not tables:
            console.print("[red]No tables found in database![/red]")
        else:
            for table in tables:
                console.print(f"\n[green]Table: {table[0]}[/green]")
                # Get table schema
                cursor.execute(f"PRAGMA table_info({table[0]})")
                columns = cursor.fetchall()
                console.print("Schema:")
                for col in columns:
                    console.print(f"  - {col[1]} ({col[2]})")

                # Get row count
                cursor.execute(f"SELECT COUNT(*) FROM {table[0]}")
                count = cursor.fetchone()[0]
                console.print(f"Row count: {count}")

                # Show sample data if any exists
                if count > 0:
                    cursor.execute(f"SELECT * FROM {table[0]} LIMIT 3")
                    rows = cursor.fetchall()
                    console.print("Sample data:")
                    for row in rows:
                        console.print(f"  {row}")

        conn.close()

    except sqlite3.Error as e:
        console.print(f"[red]SQLite Error: {str(e)}[/red]")
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")


def verify_course_creation(db_path: Path, course_code: str):
    """Verify if a specific course exists in the database."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT course_id, course_code, course_title, semester 
            FROM courses 
            WHERE course_code = ?
        """, (course_code,))

        course = cursor.fetchone()

        console.print("\n[cyan]Course Verification:[/cyan]")
        if course:
            console.print(f"[green]Course found:[/green]")
            console.print(f"ID: {course[0]}")
            console.print(f"Code: {course[1]}")
            console.print(f"Title: {course[2]}")
            console.print(f"Semester: {course[3]}")
        else:
            console.print(f"[red]Course '{course_code}' not found in database[/red]")

        conn.close()

    except sqlite3.Error as e:
        console.print(f"[red]SQLite Error: {str(e)}[/red]")


if __name__ == "__main__":
    db_path = Path("~/.gradebook/gradebook.db").expanduser()
    inspect_database(db_path)
    # verify_course_creation(db_path, "CHM343")