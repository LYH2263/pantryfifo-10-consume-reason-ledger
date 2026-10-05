import os, sqlite3
from pathlib import Path

def db_path() -> Path:
    d = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "pantryfifo.db"

def connect():
    # timeout: 等锁上限,并发确认时后到者排队而不是立刻 database is locked
    c = sqlite3.connect(db_path(), timeout=10)
    c.row_factory = sqlite3.Row
    return c
