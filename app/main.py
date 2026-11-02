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
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware

from . import admin, cas, crypto, db, ics, mailer, notify, presence, scheduler, service, widget
from .config import (
    FORCE_HTTPS,
    PUBLIC_BASE_URL,
    ROOT,
    SCHEDULER_ENABLED,
)
from .polite import MAX_CONCURRENCY
from .security import (
    RateLimiter,
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


_TOKEN_PATHS = ("/ics/", "/widget/")


def _safe_log_path(path: str) -> str:
    """订阅 / 小组件链接本身就是凭证：日志里只留前缀。"""
    for prefix in _TOKEN_PATHS:
        if path.split("?", 1)[0].startswith(prefix):
            return f"{prefix}<redacted>"
    return path


class _RedactTokenPaths(logging.Filter):
    """不能让带 token 的链接出现在 uvicorn 的访问日志里。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(_safe_log_path(arg) if isinstance(arg, str) else arg for arg in record.args)
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactTokenPaths())
admin.install_log_capture()


_upstream = ThreadPoolExecutor(max_workers=MAX_CONCURRENCY, thread_name_prefix="upstream")


async def _upstream_call(fn):
    """学校 I/O 放在专用线程池，避免占满 Starlette 默认线程池，把本地读接口堵住。"""
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    return await loop.run_in_executor(_upstream, lambda: ctx.run(fn))


@asynccontextmanager
async def lifespan(_app):
    # uvicorn.run(app) 会在导入之后才配置 logging、换掉 handler：启动时再挂一次（重复挂不会重复记录）
    admin.install_log_capture()
    db.init()
    mailer.start()
    scheduler.start(refresh=SCHEDULER_ENABLED)
    try:
        yield
    finally:
        scheduler.stop()
        mailer.stop()
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


def _widget_post(request: Request) -> bool:
    """小组件交回代抓结果 / 要日历订阅链接：Scriptable 不带 Origin，凭路径里的 token 认人、不用 cookie，
    没有 CSRF 的问题。浏览器带了别站的 Origin 仍然拒绝。"""
    return (request.method == "POST" and request.url.path.startswith("/widget/")
            and request.url.path.endswith(("/upload", "/calendar")) and not request.headers.get("origin"))


@app.middleware("http")
async def security_mw(request: Request, call_next):
    if request.url.path in ("/static/index.html", "/static/admin.html"):
        # 模板里的 {{v}} 只应由 / 渲染，避免通过静态目录拿到未替换的版本
        return PlainTextResponse("Not Found", status_code=404)
    if FORCE_HTTPS and request.url.scheme != "https":
        # 跳到配置的公网地址，不用客户端传来的 Host，也不依赖被反代剥掉的前缀
        target = PUBLIC_BASE_URL + request.url.path
        if request.url.query:
            target += "?" + request.url.query
        return RedirectResponse(target, 308)
    if request.method not in ("GET", "HEAD", "OPTIONS") and not origin_ok(request) and not _widget_post(request):
        return JSONResponse({"detail": "非法来源"}, 403)
    if request.url.path == "/" or (request.url.path.startswith("/api/")
                                   and not request.url.path.startswith("/api/admin/")):
        presence.mark_visitor(_client_ip(request))
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
        # 不带版本的旧地址（部署前打开的页面还会用到）：可重新验证，保证拿到的是新文件
        resp.headers["Cache-Control"] = "public, max-age=0, must-revalidate"
    return resp


class _CompressFrontend:
    """只压缩页面和前端资源。/api 的响应里有 CSRF token，和用户输入一起压缩会给 BREACH 留口子。"""

    def __init__(self, app):
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=1024)

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if path == "/" or path.startswith(("/assets/", "/static/")):
            return await self.gzip(scope, receive, send)
        return await self.app(scope, receive, send)


app.add_middleware(_CompressFrontend)


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
    try:
        if r["need"] == "captcha":
            r["captcha_image"] = _captcha_data_url(r["flow_id"])
        elif r["need"] == "slider":
            # 学校校验一次就作废这张图：每次需要滑块都换新的
            r["slider"] = _slider_payload(r["flow_id"])
    except service.LoginRejected as e:
        raise HTTPException(400, str(e)) from None
    return {"ok": False, **r}


def _slider_payload(flow_id: str) -> dict:
    c = service.login_slider_challenge(flow_id)
    (big, big_type), (small, small_type) = c["big"], c["small"]
    return {
        "background": f"data:{big_type};base64,{base64.b64encode(big).decode()}",
        "piece": f"data:{small_type};base64,{base64.b64encode(small).decode()}",
        "width": cas.SLIDER_CANVAS_WIDTH,
        "height": cas.SLIDER_CANVAS_HEIGHT,
    }


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


class SliderTrackPoint(BaseModel):
    a: float = Field(ge=-2000, le=2000)   # 相对按下点的横向位移
    b: float = Field(ge=-2000, le=2000)   # 纵向位移
    c: int = Field(ge=0, le=120_000)      # 距上一个采样点的毫秒数


class SliderIn(FlowIn):
    canvas_length: int = Field(ge=200, le=400)
    move_length: float = Field(ge=0, le=400)
    tracks: list[SliderTrackPoint] = Field(min_length=2, max_length=600)


def _num(v: float):
    # 浏览器的 JSON.stringify 不会写 “12.0”：整数按整数上报，和学校页面插件一致
    return int(v) if float(v).is_integer() else v


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


@app.post("/api/login/slider")
async def login_slider(body: SliderIn, response: Response):
    """用户本人在页面上拖动拼图的结果（位移 + 轨迹），原样签名后交给学校校验。"""
    tracks = [{"a": _num(t.a), "b": _num(t.b), "c": t.c} for t in body.tracks]
    return await _upstream_call(lambda: _login_result(
        lambda: service.login_submit_slider(body.flow_id, body.canvas_length, _num(body.move_length), tracks),
        response))


@app.post("/api/login/slider/refresh")
async def login_slider_refresh(body: FlowIn):
    def run():
        try:
            return {"slider": _slider_payload(body.flow_id)}
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


def _widget_url(uid: int) -> str | None:
    row = db.connect().execute("SELECT enc_widget_token FROM users WHERE id=?", (uid,)).fetchone()
    if not row or not row["enc_widget_token"]:
        return None
    try:
        token = crypto.decrypt(row["enc_widget_token"], "widget", uid).decode()
    except (crypto.CryptoError, UnicodeDecodeError):
        log.warning("user %s: widget token cannot be decrypted", uid)
        return None
    return f"{PUBLIC_BASE_URL}/widget/{token}.json"


def _record_use(uid: int) -> None:
    """有人在用（网站 / 小组件 / 日历）：更新活跃度；数据比他的目标间隔还旧就排一次后台刷新。

    纯本地数据库操作，不访问学校；出错只记日志，绝不影响请求本身。
    """
    try:
        service.touch(uid)
        service.request_if_stale(uid)
    except Exception as e:
        log.warning("record use failed for user %s: %s", uid, type(e).__name__)


def _json_list(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


@app.get("/api/me")
def me(request: Request, response: Response, sess=Depends(current_session)):
    # 每次打开页面都会先调这里：顺手做会话滑动续期，记下“还在用”，数据过期就排一次后台刷新
    renew_session(request, response, sess)
    _record_use(sess["id"])
    outlook = service.refresh_outlook(sess["id"])
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
        "next_refresh_at": outlook.get("next_refresh_at"),
        "refresh_target_minutes": outlook.get("target_minutes"),
        "refresh_reason": outlook.get("reason"),
        "refresh_pending": outlook.get("pending", False),
        "current_semester_id": sess["current_semester_id"],
        "semesters": _json_list(sess["semesters_json"]),
        "sso_short": service.sso_short(sess["cred_mode"], sess["sso_session_only"]),
        "keepalive": service.keepalive_state(sess),
        "ics_url": _ics_url(sess["id"]),
        "mail": notify.snapshot(sess["id"]),
        "widget_url": _widget_url(sess["id"]),
        "widget_fetch": _widget_fetch_enabled(sess["id"]),
    }


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

# ---------- 邮件通知 ----------

class EmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class EmailCodeIn(BaseModel):
    code: str = Field(min_length=4, max_length=10)


class EmailPrefsIn(BaseModel):
    notify_status: bool
    notify_changes: bool


def _email_call(fn):
    try:
        return fn()
    except notify.EmailError as e:
        headers = {"Retry-After": str(e.retry_after)} if e.retry_after else None
        raise HTTPException(e.status, str(e), headers=headers) from None
    except KeyError:
        raise HTTPException(401, "会话已失效") from None


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


@app.post("/api/email/start")
def email_start(body: EmailIn, request: Request, sess=Depends(csrf_protected)):
    return _email_call(lambda: notify.start_verification(sess["id"], body.email, _client_ip(request)))


@app.post("/api/email/verify")
def email_verify(body: EmailCodeIn, sess=Depends(csrf_protected)):
    return _email_call(lambda: notify.confirm(sess["id"], body.code))


@app.post("/api/email/remove")
def email_remove(sess=Depends(csrf_protected)):
    return _email_call(lambda: notify.remove(sess["id"]))


@app.post("/api/email/prefs")
def email_prefs(body: EmailPrefsIn, sess=Depends(csrf_protected)):
    return _email_call(lambda: notify.set_prefs(sess["id"], body.notify_status, body.notify_changes))


@app.post("/api/email/test")
def email_test(request: Request, sess=Depends(csrf_protected)):
    return _email_call(lambda: notify.send_test(sess["id"], _client_ip(request)))


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


# ---------- iOS 桌面小组件（Scriptable） ----------

WIDGET_SCRIPT = ROOT / "scriptable" / "nufe-kebiao-widget.js"
_script_cache: tuple[int, str, str] | None = None


def script_version(template: str) -> str:
    """脚本版本 = 模板内容的哈希：改了脚本就自动换版本，不用手工维护。

    以字母开头：比旧脚本的日期版本（"2026.11.04"）按字符串比较更大，老用户也会看到“可更新”。
    """
    return "sha-" + hashlib.sha256(template.encode()).hexdigest()[:12]


def _widget_template() -> tuple[str, str]:
    """(填好版本号的脚本模板, 版本号)；按 mtime 缓存，部署新脚本后不用重启。"""
    global _script_cache
    mtime = WIDGET_SCRIPT.stat().st_mtime_ns
    if not _script_cache or _script_cache[0] != mtime:
        text = WIDGET_SCRIPT.read_text("utf-8")
        version = script_version(text)
        _script_cache = (mtime, text.replace("__SCRIPT_VERSION__", version), version)
    return _script_cache[1], _script_cache[2]


def _widget_user(token: str):
    if not token or len(token) > 128:
        raise HTTPException(404)
    user = db.connect().execute(
        "SELECT id, status FROM users WHERE widget_token_hash=?", (crypto.token_hash(token),)).fetchone()
    if not user:
        raise HTTPException(404)
    return user


_WIDGET_HEADERS = {"X-Robots-Tag": "noindex, nofollow, noarchive"}


@app.post("/api/widget/rotate")
def widget_rotate(sess=Depends(csrf_protected)):
    token = secrets.token_urlsafe(24)
    with db.tx() as c:
        if not c.execute("SELECT 1 FROM users WHERE id=?", (sess["id"],)).fetchone():
            raise HTTPException(401, "会话已失效")
        c.execute("UPDATE users SET widget_token_hash=?, enc_widget_token=? WHERE id=?",
                  (crypto.token_hash(token), crypto.encrypt(token.encode(), "widget", sess["id"]), sess["id"]))
    return {"widget_url": f"{PUBLIC_BASE_URL}/widget/{token}.json"}


def _widget_fetch_enabled(uid: int) -> bool:
    row = db.connect().execute("SELECT widget_fetch FROM users WHERE id=?", (uid,)).fetchone()
    return bool(row and row["widget_fetch"])


class WidgetFetchIn(BaseModel):
    enabled: bool


@app.post("/api/widget/fetch-pref")
def widget_fetch_pref(body: WidgetFetchIn, sess=Depends(csrf_protected)):
    service.set_widget_fetch(sess["id"], body.enabled)
    return {"widget_fetch": body.enabled}


@app.post("/api/widget/disable")
def widget_disable(sess=Depends(csrf_protected)):
    with db.tx() as c:
        c.execute("UPDATE users SET widget_token_hash=NULL, enc_widget_token=NULL WHERE id=?", (sess["id"],))
    return {"ok": True}


@app.get("/api/widget/script")
def widget_script(sess=Depends(current_session)):
    """把本人的只读链接填进 Scriptable 脚本模板，设置页一键复制。"""
    url = _widget_url(sess["id"])
    if not url:
        raise HTTPException(404, "请先生成小组件链接")
    template, _version = _widget_template()
    return PlainTextResponse(template.replace("__WIDGET_URL__", url),
                             media_type="text/javascript; charset=utf-8", headers={"Cache-Control": "no-store"})


@app.get("/widget/{token}.js")
def widget_script_by_token(token: str):
    """脚本在 Scriptable 里自我更新用：凭同一个只读 token 取最新脚本（已填好本人链接）。"""
    _widget_user(token)
    template, _version = _widget_template()
    return PlainTextResponse(template.replace("__WIDGET_URL__", f"{PUBLIC_BASE_URL}/widget/{token}.json"),
                             media_type="text/javascript; charset=utf-8",
                             headers={"Cache-Control": "no-store", **_WIDGET_HEADERS})


@app.get("/widget/{token}.json")
def widget_feed(token: str, refresh: int = Query(0, ge=0, le=1)):
    user = _widget_user(token)
    presence.mark_user(user["id"], "widget")
    try:
        service.touch(user["id"])
        # ?refresh=1：脚本判断快上课，或用户在 Scriptable 里点了“刷新课表”。
        # 普通打开仍只在超过目标间隔时排队（旧脚本也靠这个）。
        if refresh:
            service.push_widget_refresh(user["id"])
        else:
            service.request_if_stale(user["id"])
    except Exception as e:
        log.warning("record use failed for user %s: %s", user["id"], type(e).__name__)
    view = service.timetable_view(user["id"], None)
    week1 = view.get("week1_monday") if view else None
    _template, version = _widget_template()
    payload = widget.build(view, week1, status=user["status"], script_version=version,
                           changes=service.recent_changes(user["id"]))
    try:
        payload["refresh"] = service.widget_refresh_state(user["id"])
    except Exception as e:
        log.warning("widget refresh state failed for user %s: %s", user["id"], type(e).__name__)
        payload["refresh"] = {"pending": False, "due": False, "fresh": False}
    try:
        task = service.widget_fetch_task(user["id"])
    except Exception as e:
        log.warning("widget fetch task failed for user %s: %s", user["id"], type(e).__name__)
        task = None
    if task:
        payload["fetch"] = task
    payload["delegate"] = _widget_fetch_enabled(user["id"])
    # 正在排队 / 带着代抓任务（含 cookie）时不要缓存，两分钟后的重画必须打到服务器
    caching = "private, no-store" if payload["refresh"].get("pending") or task else "private, max-age=60"
    return JSONResponse(payload, headers={"Cache-Control": caching, **_WIDGET_HEADERS})


def _widget_payload(user) -> dict:
    view = service.timetable_view(user["id"], None)
    _template, version = _widget_template()
    payload = widget.build(view, view.get("week1_monday") if view else None, status=user["status"],
                           script_version=version, changes=service.recent_changes(user["id"]))
    payload["refresh"] = service.widget_refresh_state(user["id"])
    payload["delegate"] = _widget_fetch_enabled(user["id"])
    return payload


WIDGET_UPLOAD_MAX = 4 * 1024 * 1024


@app.post("/widget/{token}/upload")
async def widget_upload(token: str, request: Request):
    """小组件用手机网络抓到的课表页和 print-data。响应带上最新的小组件数据，脚本同一次运行就能画上。"""
    user = await asyncio.to_thread(_widget_user, token)
    size = request.headers.get("content-length") or "0"
    if not size.isdigit() or int(size) > WIDGET_UPLOAD_MAX:
        raise HTTPException(413, "上传内容过大")
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > WIDGET_UPLOAD_MAX:
            raise HTTPException(413, "上传内容过大")
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "格式不正确") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "格式不正确")

    def run():
        try:
            result = service.accept_widget_upload(user["id"], body)
        except service.WidgetUploadRejected as e:
            raise HTTPException(400, str(e)) from None
        except service.UserGone:
            raise HTTPException(404) from None
        fresh = db.connect().execute("SELECT id, status FROM users WHERE id=?", (user["id"],)).fetchone()
        return {**result, "widget": _widget_payload(fresh)}

    out = await asyncio.to_thread(run)
    return JSONResponse(out, headers={"Cache-Control": "no-store", **_WIDGET_HEADERS})


@app.post("/widget/{token}/calendar")
def widget_calendar(token: str):
    """小组件“订阅到日历”：给出本人的 ICS 订阅链接，还没生成就顺手生成一个。
    小组件链接已经能读到完整课表，ICS 链接是同等的只读权限；已有的链接原样返回，不会让别处的订阅失效。"""
    user = _widget_user(token)
    url = _ics_url(user["id"])
    if not url:
        new = secrets.token_urlsafe(24)
        # 事务里再看一眼（BEGIN IMMEDIATE 已串行）：两次点击同时到达，后到的那次不能把先发出去的链接顶掉
        with db.tx() as c:
            if _ics_url(user["id"]) is None:
                c.execute("UPDATE users SET ics_token_hash=?, enc_ics_token=? WHERE id=?",
                          (crypto.token_hash(new), crypto.encrypt(new.encode(), "ics", user["id"]), user["id"]))
        url = _ics_url(user["id"])
        if not url:
            raise HTTPException(404)
    return JSONResponse({"ics_url": url}, headers={"Cache-Control": "no-store", **_WIDGET_HEADERS})


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
    presence.mark_user(user["id"], "ics")
    _record_use(user["id"])
    # ?alarm=N：每节课带课前 N 分钟的提醒（安卓 ICSx⁵、Outlook 等会照着响）；不写就和以前一样不带
    version, body = service.ics_body(user["id"], user["student_id"],
                                     ics.parse_alarm(request.query_params.get("alarm")))
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
    base = f"assets/{_asset_version()}"
    # 一次列出全部模块，浏览器并行下载，不用等上一层 import 解析完才发现下一层
    preload = "\n  ".join(
        f'<link rel="modulepreload" href="{base}/{p.relative_to(STATIC).as_posix()}">'
        for p in _static_files() if p.suffix == ".js" and p.relative_to(STATIC).parts[:1] == ("js",)
        and p.name not in ("theme-init.js", "main.js"))
    return html.replace("{{modulepreload}}", preload).replace("{{assets}}", base)


@app.get("/")
def index():
    # 静态资源 URL 带内容哈希；页面本身不缓存
    return HTMLResponse(_render_index(), headers={"Cache-Control": "no-cache"})


# ---------- 服务器管理员页 ----------

admin_login_by_ip = RateLimiter(5, 900)


class AdminLoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@app.get("/admin")
def admin_page():
    if not admin.ENABLED:
        raise HTTPException(404)
    html = (STATIC / "admin.html").read_text("utf-8").replace("{{assets}}", f"assets/{_asset_version()}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"})


@app.post("/api/admin/login")
def admin_login(body: AdminLoginIn, request: Request, response: Response):
    if not admin.ENABLED:
        raise HTTPException(404)
    ip = _client_ip(request)
    if not admin_login_by_ip.hit(ip):
        raise HTTPException(429, "尝试过于频繁，请 15 分钟后再试")
    if not admin.verify_password(body.password, admin.ADMIN_PASSWORD_HASH):
        log.warning("admin login failed from %s", ip)
        raise HTTPException(401, "密码错误")
    log.info("admin login from %s", ip)
    return {"csrf": admin.login(response)}


@app.post("/api/admin/logout")
def admin_logout(request: Request, response: Response, _csrf=Depends(admin.admin_csrf)):
    admin.logout(request, response)
    return {"ok": True}


@app.get("/api/admin/me")
def admin_me(csrf=Depends(admin.current_admin)):
    return {"csrf": csrf}


@app.get("/api/admin/overview")
def admin_overview(_csrf=Depends(admin.current_admin)):
    return admin.overview()


@app.get("/api/admin/logs")
def admin_logs(after: int = Query(0, ge=0), level: str | None = Query(None, pattern="^(INFO|WARNING|ERROR)$"),
               q: str | None = Query(None, max_length=100), limit: int = Query(500, ge=1, le=2000),
               _csrf=Depends(admin.current_admin)):
    return {"items": admin.ring.since(after, level, q, limit), "seq": admin.ring.seq,
            "capacity": admin.LOG_CAPACITY}


@app.get("/api/admin/schedule")
def admin_schedule(_csrf=Depends(admin.current_admin)):
    return admin.schedule()


@app.get("/api/admin/refreshes")
def admin_refreshes(_csrf=Depends(admin.current_admin)):
    return admin.recent_refreshes()


class AdminRefreshIn(BaseModel):
    uid: int = Field(ge=1)


@app.post("/api/admin/request-refresh")
def admin_request_refresh(body: AdminRefreshIn, _csrf=Depends(admin.admin_csrf)):
    """把这个人排到队首（仍受全站预算约束，不会绕过对学校的限速）。"""
    service.request_refresh(body.uid, "管理员手动排队")
    return {"ok": True}


_assets = StaticFiles(directory=STATIC)


@app.get("/assets/{version}/{path:path}")
async def versioned_asset(version: str, path: str, request: Request):
    # 版本号放在路径里，模块之间的相对 import 自动带上同一个版本，整套资源都能长期缓存
    if not path or path.rsplit("/", 1)[-1] in ("index.html", "admin.html") or any(part.startswith(".") for part in path.split("/")):
        return PlainTextResponse("Not Found", status_code=404)
    try:
        resp = await _assets.get_response(path, request.scope)
    except StarletteHTTPException:
        return PlainTextResponse("Not Found", status_code=404)
    if resp.status_code in (200, 304):
        # 版本对不上（部署前打开的旧页面）时给的是新文件，不能让它冒充旧版本被长期缓存
        resp.headers["Cache-Control"] = ("public, max-age=31536000, immutable" if version == _asset_version()
                                         else "no-cache")
    return resp


@app.get("/health")
@app.get("/healthz")
def healthz():
    return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
