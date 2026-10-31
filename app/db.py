from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .config import DB_PATH

log = logging.getLogger("kebiao.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY,
    student_id       TEXT NOT NULL UNIQUE,
    name             TEXT,
    created_at       REAL NOT NULL,
    cred_mode        TEXT NOT NULL DEFAULT 'cookie',   -- cookie | password
    enc_cookies      BLOB,                             -- AES-GCM(CAS/教务 cookie JSON)
    enc_password     BLOB,                             -- AES-GCM(教务密码)，仅 password 模式
    password_consent_at REAL,
    status           TEXT NOT NULL DEFAULT 'ok',       -- ok | error | captcha | needs_rebind
    fail_count       INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT,
    last_refresh_at  REAL,
    last_success_at  REAL,
    next_refresh_at  REAL,
    current_semester_id INTEGER,
    semesters_json   TEXT,
    ics_token_hash   TEXT UNIQUE,
    enc_ics_token    BLOB,
    enc_device_id    BLOB,             -- 本服务作为该用户"设备"的指纹，二次认证/信任设备绑定于此
    refresh_hours    TEXT              -- 用户自选的每日刷新整点（JSON 列表）；NULL = 按默认间隔
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS timetables (
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    semester_id  INTEGER NOT NULL,
    semester_name TEXT,
    data_json    TEXT NOT NULL,
    fetched_at   REAL NOT NULL,
    PRIMARY KEY (user_id, semester_id)
);
CREATE TABLE IF NOT EXISTS semesters (
    semester_id  INTEGER PRIMARY KEY,
    name         TEXT,
    week1_monday TEXT          -- YYYY-MM-DD（教务接口给出的；配置文件可覆盖）
);
CREATE TABLE IF NOT EXISTS refresh_log (
    id       INTEGER PRIMARY KEY,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    at       REAL NOT NULL,
    trigger  TEXT NOT NULL,     -- login | manual | scheduled
    ok       INTEGER NOT NULL,
    message  TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_next ON users(next_refresh_at);
CREATE INDEX IF NOT EXISTS idx_log_user ON refresh_log(user_id, at);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""

_local = threading.local()


def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def connect() -> sqlite3.Connection:
    """当前线程复用的连接。不要 close()：Starlette 线程池会反复用到同一条线程。"""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
            return conn
        except sqlite3.Error:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
    conn = _open()
    _local.conn = conn
    return conn


@contextmanager
def tx():
    """BEGIN IMMEDIATE 事务。

    - 开启失败时不再执行 ROLLBACK（否则会用 "no transaction is active" 掩盖真实错误）。
    - COMMIT 失败时尝试回滚，回滚失败只记日志。
    """
    conn = connect()
    started = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        started = True
        yield conn
        conn.execute("COMMIT")
        started = False
    except BaseException:
        if started:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                log.exception("rollback failed")
        raise


def init() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = connect()
    conn.executescript(SCHEMA)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    for col, typ in (("enc_device_id", "BLOB"), ("refresh_hours", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")


def now() -> float:
    return time.time()
