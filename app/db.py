from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .config import DB_PATH, SESSION_DAYS_SHORT

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
    next_refresh_at  REAL,             -- 状态 ok：预计下次刷新（展示用）；error：退避到这之前不重试；NULL：暂停
    current_semester_id INTEGER,
    semesters_json   TEXT,
    ics_token_hash   TEXT UNIQUE,
    enc_ics_token    BLOB,
    enc_device_id    BLOB,             -- 本服务作为该用户"设备"的指纹，二次认证/信任设备绑定于此
    refresh_hours    TEXT,             -- 已废弃（旧版的自选刷新整点），保留列以兼容老库
    enc_email        BLOB,             -- AES-GCM(已验证的通知邮箱)
    email_verified_at REAL,
    notify_status    INTEGER NOT NULL DEFAULT 1,   -- 自动刷新暂停 / 连续失败时发邮件
    notify_changes   INTEGER NOT NULL DEFAULT 1,   -- 后台刷新发现课表变动时发邮件
    widget_token_hash TEXT,            -- 桌面小组件只读链接的 token 哈希（唯一索引见 init）
    enc_widget_token BLOB,
    -- 动态刷新的信号（见 refresh_policy.py）
    last_seen_at     REAL,             -- 最近一次打开网站 / 小组件 / 日历订阅
    last_change_at   REAL,             -- 最近一次刷新发现课表有变动
    next_class_at    REAL,             -- 下一节课的开始时刻（按已存课表算，过期后惰性重算）
    next_class_checked_at REAL,
    refresh_requested_at  REAL,        -- 有人请求尽快刷新（打开了过期的页面 / 同班同学课表有变动）
    refresh_request_reason TEXT
);
-- 谁上同一节课：一个人发现调课，同班其他人立刻排到队首
CREATE TABLE IF NOT EXISTS lesson_slots (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    semester_id INTEGER NOT NULL,
    slot_key    TEXT NOT NULL,
    PRIMARY KEY (user_id, semester_id, slot_key)
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    persistent  INTEGER NOT NULL DEFAULT 0   -- 1 = 勾了“保持登录”，cookie 带 max_age
);
CREATE TABLE IF NOT EXISTS email_verifications (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    enc_email   BLOB NOT NULL,      -- 待验证的邮箱（加密）
    code_hash   TEXT NOT NULL,      -- HMAC(验证码)
    sent_at     REAL NOT NULL,
    expires_at  REAL NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0
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
    for col, typ in (("enc_device_id", "BLOB"), ("refresh_hours", "TEXT"), ("enc_email", "BLOB"),
                     ("email_verified_at", "REAL"), ("notify_status", "INTEGER NOT NULL DEFAULT 1"),
                     ("notify_changes", "INTEGER NOT NULL DEFAULT 1"), ("widget_token_hash", "TEXT"),
                     ("enc_widget_token", "BLOB"), ("last_seen_at", "REAL"), ("last_change_at", "REAL"),
                     ("next_class_at", "REAL"), ("next_class_checked_at", "REAL"),
                     ("refresh_requested_at", "REAL"), ("refresh_request_reason", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
    # 老库是 ALTER 补的列，唯一索引只能在补完列之后建
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_widget ON users(widget_token_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_slots_key ON lesson_slots(semester_id, slot_key)")
    session_cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    if "persistent" not in session_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN persistent INTEGER NOT NULL DEFAULT 0")
        # 老会话没记是否“保持登录”：有效期比短会话上限还长的，就是当初勾了的
        conn.execute("UPDATE sessions SET persistent=1 WHERE expires_at - created_at > ?",
                     (SESSION_DAYS_SHORT * 86400 + 3600,))


def now() -> float:
    return time.time()
