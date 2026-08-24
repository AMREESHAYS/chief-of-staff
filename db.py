"""SQLite access. One connection helper, one initializer."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "chief.db"
SCHEMA = Path(__file__).parent / "schema.sql"


def connect(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(path=DB_PATH):
    with connect(path) as conn:
        conn.executescript(SCHEMA.read_text())
    return path


if __name__ == "__main__":
    print(f"initialized {init()}")
