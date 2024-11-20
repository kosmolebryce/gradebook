import os
import shutil
from pathlib import Path


def cleanup_and_init():
    """Clean up any bad database state and initialize properly."""
    # Define paths
    db_dir = Path("~/.gradebook").expanduser()
    db_path = db_dir / "gradebook.db"

    print(f"Cleaning up database environment...")
    print(f"Database directory: {db_dir}")
    print(f"Database file path: {db_path}")

    try:
        # Remove the entire .gradebook directory and its contents
        if db_dir.exists():
            print(f"Removing existing directory: {db_dir}")
            shutil.rmtree(db_dir)

        # Create the directory fresh
        print(f"Creating fresh directory: {db_dir}")
        db_dir.mkdir(parents=True, exist_ok=True)

        # Verify directory exists and is actually a directory
        if not db_dir.is_dir():
            raise Exception(f"{db_dir} exists but is not a directory!")

        print("\nDirectory structure cleaned and ready!")
        print(f"You can now run your gradebook commands.")

    except Exception as e:
        print(f"Error during cleanup: {str(e)}")
        raise


if __name__ == "__main__":
    cleanup_and_init()