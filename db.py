"""SQLite access. One connection helper, one initializer."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "chief.db"
SCHEMA = Path(__file__).parent / "schema.sql"


def connect(path=None):
    """Resolve DB_PATH at call time, not at import. Binding it as a default
    argument freezes it, so pointing the module at another file — which is how
    the tests avoid touching real data — silently had no effect."""
    path = path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(path=None):
    path = path or DB_PATH
    with connect(path) as conn:
        conn.executescript(SCHEMA.read_text())
    return path


if __name__ == "__main__":
    print(f"initialized {init()}")
