from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "todos.db"


def init_db() -> None:
    with sqlite3.connect(DB) as c:
        c.execute("CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY, text TEXT, done INTEGER DEFAULT 0)")


def add(text: str) -> int:
    with sqlite3.connect(DB) as c:
        cur = c.execute("INSERT INTO todos(text) VALUES (?)", (text,))
        return cur.lastrowid or 0


def list_all() -> list[tuple[int, str, int]]:
    with sqlite3.connect(DB) as c:
        return list(c.execute("SELECT id, text, done FROM todos ORDER BY id"))


if __name__ == "__main__":
    init_db()
    add("write the README")
    for row in list_all():
        print(row)
