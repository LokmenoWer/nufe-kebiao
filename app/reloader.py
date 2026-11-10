"""进程内热重载（In-Process Hot Reload）：不断连接、不丢内存登录流与后台线程，毫秒级更新业务代码。

触发方式：
1. 生产部署：`systemctl reload kebiao` 向主进程发送 SIGHUP（异步，结果看 status_file()）；
2. 管理后台：管理员在 `/admin` 页面点击「热重载」（POST /api/admin/reload）；
3. 本地开发：`.env` 设置 `HOT_RELOAD_WATCH=true` 时，后台线程监听 `app/*.py` 与 `.env` 变更自动重载。

安全保障：
- 改动线上模块前先做两道预检：`compile()` 查语法，再开子进程真正 `import app.main`（能拦住导入期异常）；
  任何一道失败，线上实例原样不动；
- 预检之后仍然失败（如重载钩子抛错）时模块可能新旧混杂，状态里 `needs_restart` 会置位，直到下一次成功或重启；
- 按模块依赖拓扑顺序依次 `importlib.reload`，内存态（待完成登录流、学校 1 RPS 限流器、实时日志缓冲、在线人数）
  原地保留，最后切换 `app.main:app` 网关内部的 FastAPI 实例。

重载不是原子的：模块字典是原地更新的，重载那几十毫秒内的并发请求可能跨新旧两版函数。

下面这些改动热重载不会生效，要走重启（deploy.sh 可用 FORCE_RESTART=1）：
- 本文件：检测到变更会直接拒绝重载，部署脚本随之退回重启；
- `main.lifespan`，以及已经在跑的循环体（`scheduler._loop`、`_qr_loop`、`mailer._worker`）：
  它们调用的函数会更新，循环本身不会；
- 依赖和 systemd 配置。
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import importlib
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from . import config

log = logging.getLogger("kebiao.reload")

APP_DIR = Path(__file__).resolve().parent

# 严格按依赖从底层到顶层排列，确保上层模块 reload 时拿到下层已更新的符号
_RELOAD_ORDER = (
    "app.config",
    "app.crypto",
    "app.db",
    "app.polite",
    "app.presence",
    "app.mailer",
    "app.ics",
    "app.widget",
    "app.refresh_policy",
    "app.cas",
    "app.jwxt",
    "app.security",
    "app.mobile_devices",
    "app.android_releases",
    "app.notify",
    "app.service",
    "app.scheduler",
    "app.assist",
    "app.admin",
    "app.main",
)

PREFLIGHT_TIMEOUT = 30.0
_PREFLIGHT_CODE = "import app.main"


class ReloadError(Exception):
    """热重载预检或执行失败。"""


_reload_lock = threading.Lock()
_swapping_done = threading.Event()
_swapping_done.set()
_inflight_cond = threading.Condition()
_inflight_requests = 0
_in_request_ctx: contextvars.ContextVar[bool] = contextvars.ContextVar("_in_request_ctx", default=False)
_generation = 1
_last_reload_at: float | None = None
_last_reload_trigger: str | None = None
_last_reload_ms: float | None = None
_last_reload_error: str | None = None
_needs_restart = False
_version_cache: tuple[tuple, str] | None = None
_post_reload_hooks: list[Callable[[], None]] = []

_watch_stop = threading.Event()
_watch_thread: threading.Thread | None = None
_watch_lock = threading.Lock()
_sighup_installed = False

# 本文件不在 _RELOAD_ORDER 里（不能重载正在执行重载的代码），改了只能重启
_self_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _py_files() -> list[Path]:
    return sorted(p for p in APP_DIR.glob("*.py") if p.is_file() and not p.name.startswith("."))


def _files_signature() -> tuple:
    files = _py_files()
    env = config.ROOT / ".env"
    items = []
    for p in (*files, env):
        try:
            st = p.stat()
            items.append((p.name, st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    return tuple(items)


def code_version() -> str:
    """磁盘上 app/*.py 的整体内容哈希（按文件 mtime 缓存）。注意：不代表正在运行的代码，见 status()。"""
    global _version_cache
    files = _py_files()
    sig = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    if _version_cache and _version_cache[0] == sig:
        return _version_cache[1]
    h = hashlib.sha256()
    for p in files:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    ver = "sha-" + h.hexdigest()[:12]
    _version_cache = (sig, ver)
    return ver


def _disk_version() -> str | None:
    """部署同步文件时可能读到一半消失的文件：拿不到就返回 None，不让管理页跟着报错。"""
    try:
        return code_version()
    except OSError:
        return None


_loaded_version: str | None = _disk_version()


def _compile_check() -> None:
    """先编译全部源码：存在语法错误或缩进错误时绝不触碰正在运行的模块。"""
    for path in _py_files():
        try:
            source = path.read_text("utf-8")
            compile(source, str(path), "exec")
        except SyntaxError as e:
            raise ReloadError(f"语法错误（{path.name}:{e.lineno}）：{e.msg}") from None
        except (OSError, ValueError) as e:
            raise ReloadError(f"无法读取文件 {path.name}：{type(e).__name__}") from None


def _check_self_unchanged() -> None:
    try:
        current = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError as e:
        raise ReloadError(f"无法读取 reloader.py：{type(e).__name__}") from None
    if current != _self_hash:
        raise ReloadError("reloader.py 已变更，热重载无法更新它自己，需要重启服务")


def _preflight() -> None:
    """在子进程里完整导入新代码。来自 .env 的变量不传下去，让子进程按新的 .env 读配置。"""
    env = {k: v for k, v in os.environ.items() if k not in config._dotenv_loaded}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run(
            [sys.executable, "-c", _PREFLIGHT_CODE], cwd=config.ROOT, env=env, capture_output=True,
            text=True, errors="replace", timeout=PREFLIGHT_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise ReloadError(f"预检导入超过 {PREFLIGHT_TIMEOUT:.0f} 秒") from None
    except OSError as e:
        raise ReloadError(f"无法启动预检进程：{type(e).__name__}") from None
    if result.returncode != 0:
        lines = [ln.strip() for ln in result.stderr.splitlines() if ln.strip()]
        raise ReloadError(f"预检导入失败：{(lines[-1] if lines else f'退出码 {result.returncode}')[:300]}")


def register_post_reload(hook: Callable[[], None]) -> None:
    """注册热重载完成后的回调（例如演示模式重新挂载打桩函数）。"""
    if hook not in _post_reload_hooks:
        _post_reload_hooks.append(hook)


def wait_if_swapping(timeout: float = 5.0) -> None:
    """模块交换窗口期暂停新请求进入，防止并发请求读到半新半旧的模块状态。"""
    _swapping_done.wait(timeout)


def enter_request() -> contextvars.Token:
    global _inflight_requests
    with _inflight_cond:
        _inflight_requests += 1
    return _in_request_ctx.set(True)


def exit_request(token: contextvars.Token) -> None:
    global _inflight_requests
    _in_request_ctx.reset(token)
    with _inflight_cond:
        _inflight_requests = max(0, _inflight_requests - 1)
        _inflight_cond.notify_all()


def _drain_inflight(timeout: float = 2.0) -> None:
    """等待已进入网关的并发请求排空（若重载由当前 HTTP 请求自身触发，则保留自身 1 个名额）。"""
    keep = 1 if _in_request_ctx.get() else 0
    deadline = time.monotonic() + timeout
    with _inflight_cond:
        while _inflight_requests > keep:
            rem = deadline - time.monotonic()
            if rem <= 0:
                break
            _inflight_cond.wait(rem)


def _swap_modules() -> None:
    importlib.invalidate_caches()  # 否则运行中新增的模块可能因目录缓存而导入失败
    snapshots: dict[str, dict] = {}
    for mod_name in _RELOAD_ORDER:
        mod = sys.modules.get(mod_name)
        if mod is not None:
            snapshots[mod_name] = dict(mod.__dict__)
    _swapping_done.clear()
    _drain_inflight()
    try:
        try:
            for mod_name in _RELOAD_ORDER:
                mod = sys.modules.get(mod_name)
                if mod is not None:
                    importlib.reload(mod)
                else:
                    importlib.import_module(mod_name)
        except Exception:
            for mod_name, saved_dict in snapshots.items():
                mod = sys.modules.get(mod_name)
                if mod is not None:
                    mod.__dict__.clear()
                    mod.__dict__.update(saved_dict)
            raise

        from . import admin, db, mailer, scheduler

        admin.install_log_capture()
        db.init()
        mailer.start()
        # 没启动过（如单测里没走 lifespan）或正在关停时 alive() 为空，不在这里拉起线程
        if scheduler.alive():
            scheduler.start(refresh=config.SCHEDULER_ENABLED)
        start_watcher()

        for hook in list(_post_reload_hooks):
            hook()
    finally:
        _swapping_done.set()


def reload(trigger: str = "manual", wait: bool = False) -> dict:
    """执行一次进程内热重载。成功返回最新状态字典，失败抛 ReloadError。

    wait=False 时已有重载在跑就直接拒绝（管理员点按钮）；wait=True 时排队（信号和文件监听不能丢更新）。
    """
    global _generation, _last_reload_at, _last_reload_trigger, _last_reload_ms, _last_reload_error
    global _needs_restart, _version_cache, _loaded_version
    if not _reload_lock.acquire(blocking=wait):
        raise ReloadError("热重载正在进行中，请稍后再试")
    t0 = time.monotonic()
    swapping = False
    try:
        _version_cache = None
        ver = _disk_version()
        _check_self_unchanged()
        _compile_check()
        _preflight()
        swapping = True
        _swap_modules()

        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        _generation += 1
        _loaded_version = ver
        _last_reload_at = time.time()
        _last_reload_trigger = trigger
        _last_reload_ms = elapsed_ms
        _last_reload_error = None
        _needs_restart = False
        log.info("hot reload complete: gen=%d trigger=%s version=%s (%.1f ms)",
                 _generation, trigger, ver, elapsed_ms)
        return status()
    except ReloadError as e:
        _last_reload_error = str(e)
        log.warning("hot reload rejected (%s): %s", trigger, e)
        raise
    except Exception as e:
        _last_reload_error = f"{type(e).__name__}: {e}"
        _needs_restart = swapping
        log.error("hot reload failed (%s): %s", trigger, type(e).__name__)
        raise ReloadError(f"热重载失败：{type(e).__name__}") from e
    finally:
        _write_status()
        _reload_lock.release()


def status() -> dict:
    """热重载状态概要（管理员页、部署脚本用）。code_version 是正在运行的代码，disk_version 是磁盘上的。"""
    disk = _disk_version()
    return {
        "generation": _generation,
        "code_version": _loaded_version,
        "disk_version": disk,
        "in_sync": disk is None or disk == _loaded_version,
        "needs_restart": _needs_restart,
        "last_reload_at": _last_reload_at,
        "last_reload_trigger": _last_reload_trigger,
        "last_reload_ms": _last_reload_ms,
        "last_reload_error": _last_reload_error,
        "watch_enabled": bool(config.HOT_RELOAD_WATCH),
        "watcher_alive": bool(_watch_thread and _watch_thread.is_alive()),
    }


def status_file() -> Path:
    """每次重载尝试后写一份结果：SIGHUP 是异步的，部署脚本靠它判断重载到底成没成功。"""
    return Path(config.DB_PATH).parent / "reload-status.json"


def _write_status() -> None:
    path = status_file()
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps({**status(), "pid": os.getpid()}, ensure_ascii=False), "utf-8")
        os.replace(tmp, path)
    except OSError as e:
        log.warning("cannot write reload status: %s", type(e).__name__)


def _on_sighup(_signum, _frame) -> None:
    threading.Thread(target=_safe_bg_reload, args=("sighup",), name="sighup-reload", daemon=True).start()


def _safe_bg_reload(trigger: str) -> None:
    with contextlib.suppress(Exception):
        reload(trigger=trigger, wait=True)  # reload() 内部已记录日志与 _last_reload_error


def install_signal_handler() -> None:
    """在主线程挂载 SIGHUP 处理器（Windows 无 SIGHUP 时自动跳过）。"""
    global _sighup_installed
    if _sighup_installed or not hasattr(signal, "SIGHUP"):
        return
    if threading.current_thread() is not threading.main_thread():
        return
    signal.signal(signal.SIGHUP, _on_sighup)
    _sighup_installed = True


def _watch_loop() -> None:
    last_sig = _files_signature()
    while not _watch_stop.wait(max(0.2, float(config.HOT_RELOAD_INTERVAL))):
        if not config.HOT_RELOAD_WATCH:
            continue
        cur_sig = _files_signature()
        if cur_sig != last_sig:
            last_sig = cur_sig
            _safe_bg_reload("watch")


def start_watcher() -> None:
    global _watch_thread
    if not config.HOT_RELOAD_WATCH:
        return
    with _watch_lock:
        if _watch_thread and _watch_thread.is_alive():
            return
        _watch_stop.clear()
        _watch_thread = threading.Thread(target=_watch_loop, name="hot-reload-watcher", daemon=True)
        _watch_thread.start()
        log.info("hot-reload file watcher started (interval=%.1fs)", config.HOT_RELOAD_INTERVAL)


def stop_watcher() -> None:
    _watch_stop.set()
    with _watch_lock:
        thread = _watch_thread
    if thread:
        thread.join(timeout=2)
