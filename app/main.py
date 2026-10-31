from __future__ import annotations

import asyncio
import base64
import contextvars
import hashlib
import json
import logging
import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import requests
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import crypto, db, scheduler, service
from .config import (
    FORCE_HTTPS,
    MAX_USER_REFRESH_TIMES,
    PUBLIC_BASE_URL,
    REFRESH_ALLOWED_HOURS,
    REFRESH_INTERVAL_HOURS,
    ROOT,
    SCHEDULER_ENABLED,
)
from .polite import MAX_CONCURRENCY
from .security import (
    clear_device_hint,
    clear_session_cookie,
    create_session,
    csrf_protected,
    current_session,
    destroy_all_sessions,
    destroy_session,
    device_hint_uid,
    login_by_ip,
    login_by_student,
    origin_ok,
    renew_session,
    set_device_hint,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("urllib3").setLevel(logging.WARNING)

log = logging.getLogger("kebiao.web")


class _RedactIcsPath(logging.Filter):
    """订阅链接本身就是凭证，不能出现在 uvicorn 的访问日志里。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                "/ics/<redacted>" if isinstance(arg, str) and arg.split("?", 1)[0].startswith("/ics/") else arg
                for arg in record.args
            )
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactIcsPath())


def _safe_log_path(path: str) -> str:
    return "/ics/<redacted>" if path.startswith("/ics/") else path


_upstream = ThreadPoolExecutor(max_workers=MAX_CONCURRENCY, thread_name_prefix="upstream")


async def _upstream_call(fn):
    """学校 I/O 放在专用线程池，避免占满 Starlette 默认线程池，把本地读接口堵住。"""
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    return await loop.run_in_executor(_upstream, lambda: ctx.run(fn))


@asynccontextmanager
async def lifespan(_app):
    db.init()
    scheduler.start(refresh=SCHEDULER_ENABLED)
    try:
        yield
    finally:
        scheduler.stop()
        _upstream.shutdown(wait=False, cancel_futures=True)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
STATIC = ROOT / "static"

# 只记录类型和路径：异常原文可能带 ticket / ticket 回调地址等敏感信息
@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    log.error("unhandled error on %s: %s", _safe_log_path(request.url.path), type(exc).__name__)
    return JSONResponse({"detail": "服务内部错误，请稍后重试"}, status_code=500)


@app.exception_handler(requests.RequestException)
async def upstream_exception(request: Request, exc: requests.RequestException):
    # 学校服务器超时 / 连不上 / 本站正在给它降温：告诉用户是学校那边的问题，而不是笼统的 500
    log.warning("upstream error on %s: %s", _safe_log_path(request.url.path), type(exc).__name__)
    retry_after = getattr(exc, "retry_after", None)
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    return JSONResponse({"detail": "学校服务器暂时无法访问，请稍后再试"}, status_code=503, headers=headers)


@app.exception_handler(RequestValidationError)
async def validation_exception(_request: Request, _exc: RequestValidationError):
    # 前端只展示 detail 字符串；不要把 pydantic 的内部结构回给浏览器
    return JSONResponse({"detail": "请求参数不正确"}, status_code=422)


@app.middleware("http")
async def security_mw(request: Request, call_next):
    if request.url.path == "/static/index.html":
        # 模板里的 {{v}} 只应由 / 渲染，避免通过静态目录拿到未替换的版本
        return PlainTextResponse("Not Found", status_code=404)
    if FORCE_HTTPS and request.url.scheme != "https":
        # 跳到配置的公网地址，不用客户端传来的 Host，也不依赖被反代剥掉的前缀
        target = PUBLIC_BASE_URL + request.url.path
        if request.url.query:
            target += "?" + request.url.query
        return RedirectResponse(target, 308)
    if request.method not in ("GET", "HEAD", "OPTIONS") and not origin_ok(request):
        return JSONResponse({"detail": "非法来源"}, 403)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    resp.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; font-src 'self'; manifest-src 'self'; worker-src 'none'; "
        "frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'; "
        "require-trusted-types-for 'script'; trusted-types 'none'")
    if FORCE_HTTPS:
        # 不加 includeSubDomains：同域名下还有其他子站，不能替它们强制 HTTPS
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        resp.headers["Content-Security-Policy"] += "; upgrade-insecure-requests"
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/static/"):
        # 模块化前端的 import 语句无法逐个带 ?v=，用可重新验证的缓存保证部署后立刻生效
        resp.headers["Cache-Control"] = "public, max-age=0, must-revalidate"
    return resp


# ---------- 认证 ----------

class FlowIn(BaseModel):
    flow_id: str = Field(max_length=64)


class LoginIn(BaseModel):
    student_id: str = Field(min_length=4, max_length=20)
    password: str = Field(min_length=1, max_length=128)
    remember_password: bool = False
    keep_login: bool = False
    agree_terms: bool


@app.post("/api/login")
async def login(body: LoginIn, request: Request, response: Response):
    if not body.agree_terms:
        raise HTTPException(400, "请先阅读并同意使用说明")
    if not re.fullmatch(r"[0-9A-Za-z]+", body.student_id):
        raise HTTPException(400, "学号格式不正确")
    ip = request.client.host if request.client else "?"
    if not login_by_ip.hit(ip) or not login_by_student.hit(body.student_id):
        raise HTTPException(429, "尝试过于频繁，请 15 分钟后再试（避免触发学校账号锁定）")
    hint = device_hint_uid(request)
    return await _upstream_call(lambda: _login_result(
        lambda: service.login_start(body.student_id, body.password, body.remember_password, body.keep_login, hint),
        response))


def _login_result(step, response: Response) -> dict:
    """统一处理多步登录的返回：完成则建会话；需要验证码则把图片一并带回。"""
    try:
        r = step()
    except service.LoginRejected as e:
        raise HTTPException(400, str(e)) from None
    if "uid" in r:
        csrf = create_session(response, r["uid"], r.get("keep_login", False))
        set_device_hint(response, r["uid"])
        return {"ok": True, "csrf": csrf}
    if r["need"] == "captcha":
        r["captcha_image"] = _captcha_data_url(r["flow_id"])
    return {"ok": False, **r}


def _qr_data_url(flow_id: str) -> str:
    img, ctype = service.login_qr_image(flow_id)
    return f"data:{ctype};base64,{base64.b64encode(img).decode()}"


class QrStartIn(BaseModel):
    agree_terms: bool
    keep_login: bool = False


@app.post("/api/login/qr/start")
async def login_qr_start(body: QrStartIn, request: Request, response: Response):
    if not body.agree_terms:
        raise HTTPException(400, "请先阅读并同意使用说明")
    ip = request.client.host if request.client else "?"
    if not login_by_ip.hit(ip):
        raise HTTPException(429, "尝试过于频繁，请 15 分钟后再试")
    hint = device_hint_uid(request)

    def run():
        r = _login_result(lambda: service.login_qr_start(body.keep_login, hint), response)
        if r.get("need") == "qr":
            r["qr_image"] = _qr_data_url(r["flow_id"])
        return r

    return await _upstream_call(run)


@app.post("/api/login/qr/poll")
def login_qr_poll(body: FlowIn, response: Response):
    # 只读内存里的扫码状态，不进上游线程池
    return _login_result(lambda: service.login_qr_poll(body.flow_id), response)


@app.post("/api/login/qr/refresh")
async def login_qr_refresh(body: FlowIn, response: Response):
    def run():
        r = _login_result(lambda: service.login_qr_refresh(body.flow_id), response)
        if r.get("need") == "qr":
            r["qr_image"] = _qr_data_url(r["flow_id"])
        return r

    return await _upstream_call(run)


@app.post("/api/login/cancel")
def login_cancel(body: FlowIn):
    """用户切换登录方式 / 取消时，立即释放内存里的流程（及可能暂存的密码）。"""
    service.login_cancel(body.flow_id)
    return {"ok": True}


def _captcha_data_url(flow_id: str) -> str:
    img, ctype = service.login_captcha_image(flow_id)
    return f"data:{ctype};base64,{base64.b64encode(img).decode()}"


class CaptchaIn(FlowIn):
    captcha: str = Field(min_length=1, max_length=16)


class SmsVerifyIn(FlowIn):
    code: str = Field(min_length=4, max_length=10)
    trust_device: bool = True


@app.post("/api/login/captcha")
async def login_captcha(body: CaptchaIn, response: Response):
    return await _upstream_call(lambda: _login_result(
        lambda: service.login_submit_captcha(body.flow_id, body.captcha), response))


@app.post("/api/login/captcha-image")
async def login_captcha_image(body: FlowIn):
    def run():
        try:
            return {"captcha_image": _captcha_data_url(body.flow_id)}
        except service.LoginRejected as e:
            raise HTTPException(400, str(e)) from None

    return await _upstream_call(run)


@app.post("/api/login/sms/send")
async def login_sms_send(body: FlowIn):
    def run():
        try:
            return service.login_send_sms(body.flow_id)
        except service.LoginRejected as e:
            raise HTTPException(400, str(e)) from None

    return await _upstream_call(run)


@app.post("/api/login/sms/verify")
async def login_sms_verify(body: SmsVerifyIn, response: Response):
    return await _upstream_call(lambda: _login_result(
        lambda: service.login_verify_sms(body.flow_id, body.code, body.trust_device), response))


@app.post("/api/logout")
def logout(request: Request, response: Response, _s=Depends(csrf_protected)):
    destroy_session(request, response)
    return {"ok": True}


@app.post("/api/logout-all")
def logout_all(response: Response, sess=Depends(csrf_protected)):
    destroy_all_sessions(sess["id"], response)
    return {"ok": True}


def _ics_url(uid: int) -> str | None:
    row = db.connect().execute("SELECT enc_ics_token FROM users WHERE id=?", (uid,)).fetchone()
    if not row or not row["enc_ics_token"]:
        return None
    try:
        token = crypto.decrypt(row["enc_ics_token"], "ics", uid).decode()
    except (crypto.CryptoError, UnicodeDecodeError):
        log.warning("user %s: ics token cannot be decrypted", uid)
        return None
    return f"{PUBLIC_BASE_URL}/ics/{token}.ics"


def _json_list(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


@app.get("/api/me")
def me(request: Request, response: Response, sess=Depends(current_session)):
    # 每次打开页面都会先调这里：顺手做会话滑动续期
    renew_session(request, response, sess)
    if device_hint_uid(request) != sess["id"]:
        set_device_hint(response, sess["id"])  # 已登录的老会话也补上，下次扫码即可复用设备
    return {
        "student_id": sess["student_id"],
        "name": sess["name"],
        "csrf": sess["csrf"],
        "cred_mode": sess["cred_mode"],
        "status": sess["status"],
        "last_error": sess["last_error"],
        "fail_count": sess["fail_count"],
        "last_refresh_at": sess["last_refresh_at"],
        "last_success_at": sess["last_success_at"],
        "next_refresh_at": sess["next_refresh_at"],
        "current_semester_id": sess["current_semester_id"],
        "semesters": _json_list(sess["semesters_json"]),
        "ics_url": _ics_url(sess["id"]),
        "refresh_hours": _json_list(sess["refresh_hours"]),
        "refresh_allowed_hours": sorted(REFRESH_ALLOWED_HOURS),
        "refresh_max_times": MAX_USER_REFRESH_TIMES,
        "refresh_default_interval_hours": REFRESH_INTERVAL_HOURS,
    }


class RefreshHoursIn(BaseModel):
    hours: list[int] = Field(max_length=24)


@app.post("/api/settings/refresh-hours")
def set_refresh_hours(body: RefreshHoursIn, sess=Depends(csrf_protected)):
    try:
        nxt = service.set_refresh_hours(sess["id"], body.hours)
    except KeyError:
        raise HTTPException(401, "会话已失效") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"ok": True, "next_refresh_at": nxt}


# ---------- 课表 ----------

@app.get("/api/timetable")
def timetable(semester_id: int | None = Query(None, ge=1, le=2_000_000),
              sess=Depends(current_session)):
    view = service.timetable_view(sess["id"], semester_id)
    if not view:
        raise HTTPException(404, "该学期尚未抓取")
    return view


class RefreshIn(BaseModel):
    semester_id: int | None = Field(None, ge=1, le=2_000_000)


@app.post("/api/refresh")
async def refresh(body: RefreshIn, sess=Depends(csrf_protected)):
    uid = sess["id"]

    def run():
        try:
            result = service.refresh_user(uid, "manual", body.semester_id)
        except service.Busy:
            raise HTTPException(409, "正在刷新中") from None
        except service.TooSoon as e:
            raise HTTPException(429, f"刷新太频繁，请 {e.retry_after} 秒后再试",
                                headers={"Retry-After": str(e.retry_after)}) from None
        except KeyError:
            raise HTTPException(401, "会话已失效") from None
        if result.get("status") == "deleted":
            raise HTTPException(401, "账号已删除，请重新登录")
        result["timetable"] = service.timetable_view(uid, body.semester_id)
        result["account"] = service.account_snapshot(uid)
        return result

    return await _upstream_call(run)


@app.get("/api/refresh-log")
def refresh_log(sess=Depends(current_session)):
    rows = db.connect().execute(
        "SELECT at, trigger, ok, message FROM refresh_log WHERE user_id=? ORDER BY at DESC LIMIT 20",
        (sess["id"],)).fetchall()
    return [dict(r) for r in rows]


# ---------- 凭据 / 账号 ----------

@app.post("/api/credentials/forget")
def forget(sess=Depends(csrf_protected)):
    service.forget_password(sess["id"])
    return {"ok": True}


@app.post("/api/ics/rotate")
def ics_rotate(sess=Depends(csrf_protected)):
    token = secrets.token_urlsafe(24)
    with db.tx() as c:
        if not c.execute("SELECT 1 FROM users WHERE id=?", (sess["id"],)).fetchone():
            raise HTTPException(401, "会话已失效")
        c.execute("UPDATE users SET ics_token_hash=?, enc_ics_token=? WHERE id=?",
                  (crypto.token_hash(token), crypto.encrypt(token.encode(), "ics", sess["id"]), sess["id"]))
    return {"ics_url": f"{PUBLIC_BASE_URL}/ics/{token}.ics"}


@app.post("/api/ics/disable")
def ics_disable(sess=Depends(csrf_protected)):
    with db.tx() as c:
        c.execute("UPDATE users SET ics_token_hash=NULL, enc_ics_token=NULL WHERE id=?", (sess["id"],))
    return {"ok": True}


@app.post("/api/account/delete")
def delete_account(response: Response, sess=Depends(csrf_protected)):
    service.delete_user(sess["id"])
    clear_session_cookie(response)
    clear_device_hint(response)
    return {"ok": True}


@app.get("/ics/{token}.ics")
def ics_feed(token: str, request: Request):
    if not token or len(token) > 128:
        raise HTTPException(404)
    user = db.connect().execute(
        "SELECT id, student_id FROM users WHERE ics_token_hash=?", (crypto.token_hash(token),)).fetchone()
    if not user:
        raise HTTPException(404)
    version, body = service.ics_body(user["id"], user["student_id"])
    etag = f'W/"{version}"'
    headers = {
        "Cache-Control": "private, max-age=3600",
        "ETag": etag,
        "X-Robots-Tag": "noindex, nofollow, noarchive",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return PlainTextResponse(body, media_type="text/calendar; charset=utf-8", headers=headers)


# ---------- 前端 ----------

_asset_cache: tuple[tuple, str] | None = None


def _static_files() -> list:
    return sorted(p for p in STATIC.rglob("*")
                  if p.is_file() and p.name != "index.html" and not p.name.startswith("."))


def _asset_version() -> str:
    """static/ 下所有前端资源的整体内容哈希：任何一个文件变了，请求 URL 就全变。"""
    global _asset_cache
    files = _static_files()
    signature = tuple((str(p.relative_to(STATIC)), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    if _asset_cache and _asset_cache[0] == signature:
        return _asset_cache[1]
    h = hashlib.sha256()
    for p, (rel, _mtime, _size) in zip(files, signature, strict=True):
        h.update(rel.encode())
        h.update(p.read_bytes())
    version = h.hexdigest()[:12]
    _asset_cache = (signature, version)
    return version


def _render_index() -> str:
    # 每次读模板（几 KB）但内容哈希按 mtime 缓存：本地改前端不用重启服务
    html = (STATIC / "index.html").read_text("utf-8")
    return html.replace("{{v}}", _asset_version())


@app.get("/")
def index():
    # 静态资源 URL 带内容哈希；页面本身不缓存
    return HTMLResponse(_render_index(), headers={"Cache-Control": "no-cache"})


@app.get("/health")
@app.get("/healthz")
def healthz():
    return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
