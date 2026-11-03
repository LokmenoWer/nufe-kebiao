"""安卓 App 的版本信息和安装包镜像：给连不上 GitHub 的用户用。

服务器定时读 GitHub Releases，缓存后按 GitHub 接口的字段原样给 App；安装包第一次被要时下载一份，
校验 GitHub 给的 sha256 后存到数据目录。只碰本仓库的发布，内容不用人工维护。
GitHub 暂时连不上时继续给旧结果。安装包是否可信最终由系统把关：签名不同的包无法覆盖安装。
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from pathlib import Path

import requests

from .config import DB_PATH, PUBLIC_BASE_URL

log = logging.getLogger("kebiao.android")

RELEASES_API = "https://api.github.com/repos/LokmenoWer/nufe-kebiao/releases?per_page=20"
DOWNLOAD_PREFIX = "https://github.com/LokmenoWer/nufe-kebiao/releases/download/"
TAG_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
DIGEST_RE = re.compile(r"^sha256:([0-9a-f]{64})$")
REFRESH_EVERY = 1800     # 秒：多久向 GitHub 读一次
RETRY_AFTER = 300        # 秒：读失败后隔多久再试，别让每个请求都去等 GitHub
KEEP_RELEASES = 5        # 只镜像最新的这么多个版本
MAX_APK_BYTES = 30 * 1024 * 1024
TIMEOUT = 8

CACHE_DIR = Path(DB_PATH).parent / "android-apk"

_releases: list[dict] = []
_next_refresh = 0.0
_last_ok_at: float | None = None
_last_error_at: float | None = None
_last_error: str | None = None
_refresh_lock = threading.Lock()
_download_lock = threading.Lock()


class MirrorError(Exception):
    """安装包暂时拿不到。"""


def _parse(items) -> list[dict]:
    out = []
    for r in items if isinstance(items, list) else []:
        if not isinstance(r, dict) or r.get("draft") or not TAG_RE.match(str(r.get("tag_name") or "")):
            continue
        apk = next((a for a in r.get("assets") or [] if str(a.get("name") or "").endswith(".apk")
                    and str(a.get("browser_download_url") or "").startswith(DOWNLOAD_PREFIX)
                    and DIGEST_RE.match(str(a.get("digest") or ""))), None)
        if apk:
            out.append({"tag": r["tag_name"], "prerelease": bool(r.get("prerelease")), "name": apk["name"],
                        "url": apk["browser_download_url"], "sha256": apk["digest"].split(":", 1)[1],
                        "size": apk.get("size")})
    return out[:KEEP_RELEASES]


def _load() -> list[dict]:
    """最新的版本列表。别的请求正在读 GitHub 时不排队，先用旧结果。"""
    global _releases, _next_refresh, _last_ok_at, _last_error_at, _last_error
    if time.time() >= _next_refresh and _refresh_lock.acquire(blocking=False):
        try:
            r = requests.get(RELEASES_API, timeout=TIMEOUT, headers={
                "Accept": "application/vnd.github+json", "User-Agent": "nufe-kebiao-server"})
            r.raise_for_status()
            _releases = _parse(r.json())
            _next_refresh = _last_ok_at = time.time()
            _next_refresh += REFRESH_EVERY
            _prune()
        except (requests.RequestException, ValueError) as e:
            log.warning("android releases refresh failed: %s", type(e).__name__)
            _next_refresh = _last_error_at = time.time()
            _next_refresh += RETRY_AFTER
            _last_error = type(e).__name__
        finally:
            _refresh_lock.release()
    return _releases


def feed() -> list[dict]:
    """字段和 GitHub 接口一致，App 用同一段代码解析；html_url 换成本站的下载地址。"""
    return [{"tag_name": r["tag"], "prerelease": r["prerelease"], "draft": False,
             "html_url": f"{PUBLIC_BASE_URL}/android/apk/{r['tag']}.apk",
             "assets": [{"name": r["name"], "size": r["size"]}]} for r in _load()]


def latest_stable_tag() -> str | None:
    return next((r["tag"] for r in _load() if not r["prerelease"]), None)


def sync_state() -> dict:
    """最近一次读 GitHub 的结果（不发请求）：failing 表示最后一次读失败，正在给旧结果。"""
    failing = bool(_last_error_at and _last_error_at > (_last_ok_at or 0))
    return {"last_ok_at": _last_ok_at, "last_error_at": _last_error_at,
            "last_error": _last_error if failing else None, "failing": failing}


def status() -> dict:
    """管理员页：镜像了哪些版本、安装包是否已下载到本地。"""
    releases = _load()  # 可能刚读过 GitHub，同步状态要在这之后取
    return {**sync_state(), "latest_stable": latest_stable_tag(), "releases": [
        {"tag": r["tag"], "prerelease": r["prerelease"], "name": r["name"], "size": r["size"],
         "mirrored": (CACHE_DIR / f"{r['tag']}.apk").exists()} for r in releases]}


def _prune() -> None:
    keep = {f"{r['tag']}.apk" for r in _releases}
    if _releases and CACHE_DIR.is_dir():
        for path in CACHE_DIR.glob("*.apk"):
            if path.name not in keep:
                path.unlink(missing_ok=True)


def apk_path(tag: str) -> tuple[Path, str] | None:
    """(本地文件, 下载文件名)。不在发布列表里的版本返回 None；下载或校验失败抛 MirrorError。"""
    release = next((r for r in _load() if r["tag"] == tag), None)
    if not release:
        return None
    path = CACHE_DIR / f"{tag}.apk"
    with _download_lock:
        if not path.exists():
            _download(release, path)
    return path, release["name"]


def _download(release: dict, path: Path) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    digest = hashlib.sha256()
    size = 0
    try:
        with requests.get(release["url"], timeout=TIMEOUT, stream=True) as r, tmp.open("wb") as out:
            r.raise_for_status()
            for chunk in r.iter_content(64 * 1024):
                size += len(chunk)
                if size > MAX_APK_BYTES:
                    raise MirrorError("安装包过大")
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != release["sha256"]:
            raise MirrorError("安装包校验失败")
        tmp.replace(path)
    except requests.RequestException as e:
        raise MirrorError("暂时无法获取安装包") from e
    finally:
        tmp.unlink(missing_ok=True)
