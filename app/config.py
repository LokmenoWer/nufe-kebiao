from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "on")


def _hours(spec: str) -> set[int]:
    """"0-7,12,22-23" → {0..7,12,22,23}"""
    out: set[int] = set()
    for part in filter(None, (p.strip() for p in spec.split(","))):
        a, _, b = part.partition("-")
        out.update(range(int(a), int(b or a) + 1))
    return out


_key_b64 = os.getenv("APP_SECRET_KEY", "")
if not _key_b64:
    raise RuntimeError("缺少 APP_SECRET_KEY（32 字节随机数的 base64）。生成: python -c \"import os,base64;print(base64.b64encode(os.urandom(32)).decode())\"")
APP_SECRET_KEY = base64.b64decode(_key_b64)
if len(APP_SECRET_KEY) != 32:
    raise RuntimeError("APP_SECRET_KEY 必须是 32 字节")

DB_PATH = os.getenv("DB_PATH", str(ROOT / "data" / "kebiao.db"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
COOKIE_SECURE = _bool("COOKIE_SECURE", PUBLIC_BASE_URL.startswith("https://"))
# 部署在子路径（如 https://example.com/kebiao）时，会话 cookie 只在该路径下发送
COOKIE_PATH = (urlparse(PUBLIC_BASE_URL).path.rstrip("/") or "") + "/"
FORCE_HTTPS = _bool("FORCE_HTTPS", COOKIE_SECURE)
# 登录时勾选"保持登录"：持久 cookie，SESSION_DAYS_LONG 天；不勾：浏览器会话 cookie（关闭浏览器即失效），服务端最长 SESSION_DAYS_SHORT 天
SESSION_DAYS_LONG = int(os.getenv("SESSION_DAYS_LONG", "180"))
SESSION_DAYS_SHORT = int(os.getenv("SESSION_DAYS_SHORT", "7"))

SCHEDULER_ENABLED = _bool("SCHEDULER_ENABLED", True)
REFRESH_INTERVAL_HOURS = float(os.getenv("REFRESH_INTERVAL_HOURS", "12"))
REFRESH_ALLOWED_HOURS = _hours(os.getenv("REFRESH_ALLOWED_HOURS", "0-7,12-13,22-23"))
REFRESH_MAX_FAILS = int(os.getenv("REFRESH_MAX_FAILS", "3"))
MAX_USER_REFRESH_TIMES = int(os.getenv("MAX_USER_REFRESH_TIMES", "3"))  # 用户每天最多自选几个刷新时间
SCHEDULER_TICK = int(os.getenv("SCHEDULER_TICK", "60"))

TIMEZONE = "Asia/Shanghai"

# 校历锚点：学期名或学期 id → 第 1 周周一。优先级高于教务接口返回的日期。
SEMESTERS_FILE = Path(os.getenv("SEMESTERS_FILE", str(ROOT / "semesters.json")))


_semester_cache: tuple[float, dict[str, str]] | None = None


def semester_overrides() -> dict[str, str]:
    """按文件 mtime 缓存。校历一年改一两次，课表和 ICS 却每次都会来查。"""
    global _semester_cache
    try:
        mtime = SEMESTERS_FILE.stat().st_mtime
    except FileNotFoundError:
        return {}
    if _semester_cache and _semester_cache[0] == mtime:
        return _semester_cache[1]
    data = {str(k): v for k, v in json.loads(SEMESTERS_FILE.read_text("utf-8")).items() if not str(k).startswith("_")}
    _semester_cache = (mtime, data)
    return data
