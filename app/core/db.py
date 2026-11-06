"""
db.py - SQLite (stdlib) - users / sessions / tokens / videos / attempts

thread တစ်ခုချင်းစီအတွက် connection တစ်ခုစီ (autocommit)။ WAL mode ဖြင့် web thread နှင့် worker thread တပြိုင်နက် သုံးနိုင်သည်။
"""
import sqlite3
import threading
from pathlib import Path

from app.core import config

_local = threading.local()
_init_lock = threading.Lock()
_inited = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    pw_hash TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL,
    last_login REAL,
    google_id TEXT,
    auth_provider TEXT NOT NULL DEFAULT 'local'
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created REAL NOT NULL,
    expires REAL NOT NULL,
    ua TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tokens (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    expires REAL NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    expires REAL NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS attempts (
    key TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts ON attempts(key, ts);
CREATE INDEX IF NOT EXISTS idx_videos_user ON videos(user_id, created);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""


def _migrate(c: sqlite3.Connection) -> None:
    """ရှိပြီးသား database ဟောင်းများတွင် Google login column များ ထပ်ထည့်သည် (ဒေတာ မဆုံးရှုံးပါ)။"""
    cols = {r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
    if "google_id" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN google_id TEXT")
    if "auth_provider" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN auth_provider TEXT NOT NULL DEFAULT 'local'")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google ON users(google_id) WHERE google_id IS NOT NULL")


def db_path() -> Path:
    return Path(config.DATA_DIR) / "app.db"


def conn() -> sqlite3.Connection:
    path = str(db_path())
    c = getattr(_local, "conn", None)
    if c is None or getattr(_local, "path", None) != path:
        Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("PRAGMA journal_mode = WAL")
        _local.conn, _local.path = c, path
        with _init_lock:
            if path not in _inited:
                c.executescript(SCHEMA)
                _migrate(c)
                _inited.add(path)
    return c


def init() -> None:
    conn()


def one(sql: str, args=()):
    row = conn().execute(sql, args).fetchone()
    return dict(row) if row else None


def all_(sql: str, args=()):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def run(sql: str, args=()):
    return conn().execute(sql, args)
