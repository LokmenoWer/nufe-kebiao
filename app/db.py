from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .config import DB_PATH, PASSWORD_STORAGE_ENABLED, SESSION_DAYS_SHORT

log = logging.getLogger("kebiao.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY,
    student_id       TEXT NOT NULL UNIQUE,
    name             BLOB,                             -- AES-GCM(学生姓名)
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
    refresh_request_reason TEXT,
    next_class_slots TEXT,             -- 下一节课的 slot_key 列表（JSON）：同班同学刚刷过就算检查过
    recent_changes_json BLOB,          -- AES-GCM(最近 7 天发现的调课 JSON)，小组件据此标出“调”和“停”
    -- 小组件代抓：手机用自己的网络向教务要课表，再上传给本站（见 service.widget_fetch_task）
    widget_fetch     INTEGER NOT NULL DEFAULT 1,   -- 用户是否允许
    widget_fetch_ok_at REAL,           -- 最近一次代抓成功
    widget_fetch_fails INTEGER NOT NULL DEFAULT 0, -- 连续失败次数
    widget_fetch_pending_until REAL,   -- 刚把任务交给手机：这之前服务器不抢着刷
    sso_session_only INTEGER NOT NULL DEFAULT 0,   -- 认证中心只发了会话级 CASTGC（扫码登录没有“记住我”），几小时后就续不上
    -- 会话级 CASTGC 的保活（见 service.keepalive_user）
    keepalive_at     REAL,             -- 最近一次保活（成败都记，免得反复打扰学校）
    sso_touched_at   REAL,             -- 认证中心最近一次还认这个 CASTGC（登录 / 续期 / 保活）
    sso_lost_at      REAL              -- 保活发现认证中心不认了：之后只保教务会话
);
-- 谁上同一节课：一个人发现调课，同班其他人立刻排到队首
CREATE TABLE IF NOT EXISTS lesson_slots (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    semester_id INTEGER NOT NULL,
    slot_key    TEXT NOT NULL,
    course_key  TEXT,                  -- 同一门课（课程 + 教师）：补课 / 加课没有旧安排可比，按课程找同学
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
    data_json    BLOB NOT NULL,        -- AES-GCM(课表排课与学生信息 JSON)
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
    trigger  TEXT NOT NULL,     -- login | manual | scheduled | widget（手机代抓）
    ok       INTEGER NOT NULL,
    message  TEXT
);
CREATE TABLE IF NOT EXISTS mobile_devices (
    id           TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    public_key   BLOB NOT NULL,     -- 安卓 App 的设备公钥（SPKI DER），私钥不出手机
    name         TEXT NOT NULL,
    created_at   REAL NOT NULL,
    last_seen_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS mobile_pairings (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    code_hash  TEXT NOT NULL,       -- 一次性配对码的哈希
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS widget_tasks (
    task_id     TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    semester_id INTEGER NOT NULL,
    expires_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_sessions (
    token_hash  TEXT PRIMARY KEY,
    csrf        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_login_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ip          TEXT NOT NULL,
    at          REAL NOT NULL,
    ok          INTEGER NOT NULL DEFAULT 0
);
-- 2.0 起的本地优先安卓 App：不登录本站，只上报匿名心跳（随机安装 id 的哈希、版本），供管理员统计
CREATE TABLE IF NOT EXISTS app_installs (
    id_hash        TEXT PRIMARY KEY,   -- sha256(App 生成的随机安装 id)；不含学号等任何个人信息
    version        TEXT,
    version_code   INTEGER,
    sdk            INTEGER,            -- Android API level
    first_seen_at  REAL NOT NULL,
    last_seen_at   REAL NOT NULL,
    has_timetable  INTEGER NOT NULL DEFAULT 0,
    assist         INTEGER NOT NULL DEFAULT 0,   -- 用户打开了“协助刷新”
    assist_ok      INTEGER NOT NULL DEFAULT 0,   -- 累计协助成功 / 失败次数
    assist_fail    INTEGER NOT NULL DEFAULT 0,
    assist_streak  INTEGER NOT NULL DEFAULT 0,   -- 连续中继失败：到阈值后冷却一段时间不再派活
    assist_at      REAL,                         -- 最近一次领到任务
    assist_block_until REAL
);
CREATE INDEX IF NOT EXISTS idx_app_installs_seen ON app_installs(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_mobile_devices_user ON mobile_devices(user_id);
CREATE INDEX IF NOT EXISTS idx_widget_tasks_user ON widget_tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_admin_sessions_expires ON admin_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_admin_attempts_at ON admin_login_attempts(at);
CREATE INDEX IF NOT EXISTS idx_users_next ON users(next_refresh_at);
CREATE INDEX IF NOT EXISTS idx_log_user ON refresh_log(user_id, at);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""

_local: threading.local = globals().get("_local") or threading.local()


def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -8000")
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
                     ("refresh_requested_at", "REAL"), ("refresh_request_reason", "TEXT"),
                     ("next_class_slots", "TEXT"), ("recent_changes_json", "BLOB"),
                     ("widget_fetch", "INTEGER NOT NULL DEFAULT 1"), ("widget_fetch_ok_at", "REAL"),
                     ("widget_fetch_fails", "INTEGER NOT NULL DEFAULT 0"), ("widget_fetch_pending_until", "REAL"),
                     ("sso_session_only", "INTEGER NOT NULL DEFAULT 0"), ("keepalive_at", "REAL"),
                     ("sso_touched_at", "REAL"), ("sso_lost_at", "REAL")):
        if col not in cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
    if "course_key" not in {r["name"] for r in conn.execute("PRAGMA table_info(lesson_slots)")}:
        conn.execute("ALTER TABLE lesson_slots ADD COLUMN course_key TEXT")
    # 老库是 ALTER 补的列，唯一索引只能在补完列之后建
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_widget ON users(widget_token_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_slots_key ON lesson_slots(semester_id, slot_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_slots_course ON lesson_slots(semester_id, course_key)")
    session_cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    if "persistent" not in session_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN persistent INTEGER NOT NULL DEFAULT 0")
        # 老会话没记是否“保持登录”：有效期比短会话上限还长的，就是当初勾了的
        conn.execute("UPDATE sessions SET persistent=1 WHERE expires_at - created_at > ?",
                     (SESSION_DAYS_SHORT * 86400 + 3600,))
    from . import crypto
    with tx() as c:
        for r in c.execute("SELECT id, name, recent_changes_json FROM users WHERE typeof(name)='text' OR typeof(recent_changes_json)='text'").fetchall():
            if isinstance(r["name"], str):
                enc_n = crypto.encrypt(r["name"].encode(), "user_name", r["id"]) if r["name"] else None
                c.execute("UPDATE users SET name=? WHERE id=?", (enc_n, r["id"]))
            if isinstance(r["recent_changes_json"], str):
                enc_c = crypto.encrypt(r["recent_changes_json"].encode(), "changes", r["id"])
                c.execute("UPDATE users SET recent_changes_json=? WHERE id=?", (enc_c, r["id"]))
        for r in c.execute("SELECT user_id, semester_id, data_json FROM timetables WHERE typeof(data_json)='text'").fetchall():
            if isinstance(r["data_json"], str):
                enc_d = crypto.encrypt(r["data_json"].encode(), "timetable", r["user_id"])
                c.execute("UPDATE timetables SET data_json=? WHERE user_id=? AND semester_id=?",
                          (enc_d, r["user_id"], r["semester_id"]))
        if not PASSWORD_STORAGE_ENABLED:
            c.execute("UPDATE users SET cred_mode='cookie', enc_password=NULL, password_consent_at=NULL "
                      "WHERE enc_password IS NOT NULL OR cred_mode='password'")
        else:
            for r in c.execute("SELECT id, student_id, enc_password FROM users WHERE enc_password IS NOT NULL").fetchall():
                raw_pw = r["enc_password"]
                if isinstance(raw_pw, bytes | bytearray | memoryview) and bytes(raw_pw)[:1] == b"\x01":
                    migrated = crypto.migrate_legacy_password(bytes(raw_pw), r["id"], r["student_id"])
                    if migrated is not None:
                        c.execute("UPDATE users SET enc_password=? WHERE id=?", (migrated, r["id"]))


def now() -> float:
    return time.time()
