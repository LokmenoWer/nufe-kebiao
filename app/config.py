from __future__ import annotations

import base64
import binascii
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

log = logging.getLogger("kebiao.config")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(f"{name} 必须是整数，当前值：{raw!r}") from None
    if value < minimum:
        raise RuntimeError(f"{name} 不能小于 {minimum}")
    return value


def _float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        raise RuntimeError(f"{name} 必须是数字，当前值：{raw!r}") from None
    if value < minimum:
        raise RuntimeError(f"{name} 不能小于 {minimum}")
    return value


_key_b64 = os.getenv("APP_SECRET_KEY", "")
if not _key_b64:
    raise RuntimeError(
        "缺少 APP_SECRET_KEY（32 字节随机数的 base64）。"
        '生成: python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"')
try:
    APP_SECRET_KEY = base64.b64decode(_key_b64, validate=True)
except (binascii.Error, ValueError):
    raise RuntimeError("APP_SECRET_KEY 不是合法的 base64") from None
if len(APP_SECRET_KEY) != 32:
    raise RuntimeError("APP_SECRET_KEY 必须是 32 字节")

_db_path = os.getenv("DB_PATH", str(ROOT / "data" / "kebiao.db"))
DB_PATH = _db_path if os.path.isabs(_db_path) else str((ROOT / _db_path).resolve())

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
_public = urlparse(PUBLIC_BASE_URL)
if _public.scheme not in ("http", "https") or not _public.hostname:
    raise RuntimeError("PUBLIC_BASE_URL 必须是 http(s)://主机名[/路径] 形式的地址")
if _public.query or _public.fragment:
    raise RuntimeError("PUBLIC_BASE_URL 不能带查询参数或片段")

COOKIE_SECURE = _bool("COOKIE_SECURE", PUBLIC_BASE_URL.startswith("https://"))
# 部署在子路径（如 https://example.com/kebiao）时，会话 cookie 只在该路径下发送
COOKIE_PATH = (_public.path.rstrip("/") or "") + "/"
FORCE_HTTPS = _bool("FORCE_HTTPS", COOKIE_SECURE)
# 登录时勾选"保持登录"：持久 cookie，SESSION_DAYS_LONG 天；
# 不勾：浏览器会话 cookie（关闭浏览器即失效），服务端最长 SESSION_DAYS_SHORT 天
SESSION_DAYS_LONG = _int("SESSION_DAYS_LONG", 180, minimum=1)
SESSION_DAYS_SHORT = _int("SESSION_DAYS_SHORT", 7, minimum=1)

SCHEDULER_ENABLED = _bool("SCHEDULER_ENABLED", True)
REFRESH_MAX_FAILS = _int("REFRESH_MAX_FAILS", 3, minimum=1)
# 后台刷新每秒最多花掉多少次对学校的请求（全站硬上限是 1 次/秒，留出余量给登录和手动刷新）。
# 学校出错 / 变慢时调度器会自动减半，恢复后逐步加回这个值。
REFRESH_BUDGET_RPS = _float("REFRESH_BUDGET_RPS", 0.65, minimum=0.05)
if REFRESH_BUDGET_RPS > 0.9:
    raise RuntimeError("REFRESH_BUDGET_RPS 不能超过 0.9（全站上限 1 次/秒，要给登录和手动刷新留余量）")
SCHEDULER_TICK = _int("SCHEDULER_TICK", 30, minimum=5)
# 手动刷新：两次之间至少隔几秒（无论成败）；课表在多少秒内刚抓过就直接用已存的，不再打扰学校
MANUAL_REFRESH_MIN_GAP = _int("MANUAL_REFRESH_MIN_GAP", 20)
MANUAL_REFRESH_FRESH_SECONDS = _int("MANUAL_REFRESH_FRESH_SECONDS", 120)

# 邮件通知（SMTP）。变量名沿用常见的 MAIL_* 写法；没配 MAIL_HOST 时整个功能在界面上隐藏。
# MAIL_MAILER=log：不真的发信，只写日志（本地开发 / 演示用）。
MAIL_MAILER = os.getenv("MAIL_MAILER", "smtp").strip().lower()
if MAIL_MAILER not in ("smtp", "log"):
    raise RuntimeError("MAIL_MAILER 只能是 smtp 或 log")
MAIL_HOST = os.getenv("MAIL_HOST", "").strip()
MAIL_PORT = _int("MAIL_PORT", 465, minimum=1)
MAIL_USERNAME = os.getenv("MAIL_USERNAME", "").strip()
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
MAIL_ENCRYPTION = os.getenv("MAIL_ENCRYPTION", "ssl").strip().lower()
if MAIL_ENCRYPTION not in ("ssl", "tls", "starttls", "none", ""):
    raise RuntimeError("MAIL_ENCRYPTION 只能是 ssl / tls / none")
MAIL_FROM_ADDRESS = os.getenv("MAIL_FROM_ADDRESS", "").strip() or MAIL_USERNAME
MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "南财课表").strip()
MAIL_ENABLED = MAIL_MAILER == "log" or bool(MAIL_HOST and MAIL_FROM_ADDRESS)
# 两封信之间至少隔几秒：163 等免费邮箱对发信频率很敏感
MAIL_MIN_INTERVAL = _float("MAIL_MIN_INTERVAL", 3.0, minimum=0.0)

TIMEZONE = "Asia/Shanghai"

# 校历锚点：学期名或学期 id → 第 1 周周一。优先级高于教务接口返回的日期。
_semesters_path = Path(os.getenv("SEMESTERS_FILE", str(ROOT / "semesters.json")))
SEMESTERS_FILE = _semesters_path if _semesters_path.is_absolute() else (ROOT / _semesters_path).resolve()


_semester_cache: tuple[float, dict[str, str]] | None = None


def semester_overrides() -> dict[str, str]:
    """按文件 mtime 缓存。校历一年改一两次，课表和 ICS 却每次都会来查。

    文件损坏时只记一条警告并退化为空字典，不能让首页和刷新接口一起 500。
    """
    global _semester_cache
    try:
        mtime = SEMESTERS_FILE.stat().st_mtime_ns
    except FileNotFoundError:
        return {}
    except OSError as e:
        log.warning("cannot stat %s: %s", SEMESTERS_FILE, type(e).__name__)
        return {}
    if _semester_cache and _semester_cache[0] == mtime:
        return _semester_cache[1]
    try:
        data = json.loads(SEMESTERS_FILE.read_text("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        cleaned = {str(k): str(v) for k, v in data.items() if not str(k).startswith("_")}
    except (OSError, ValueError) as e:
        log.warning("校历文件 %s 无法解析（%s），将使用教务系统返回的日期", SEMESTERS_FILE, type(e).__name__)
        cleaned = {}
    _semester_cache = (mtime, cleaned)
    return cleaned
