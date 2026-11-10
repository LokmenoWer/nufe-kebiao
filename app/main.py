from __future__ import annotations

import asyncio
import base64
import contextvars
import hashlib
import hmac
import json
import logging
import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from urllib.parse import quote

import requests
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware

from . import (
    admin,
    android_releases,
    assist,
    cas,
    crypto,
    db,
    ics,
    mailer,
    mobile_devices,
    notify,
    presence,
    reloader,
    scheduler,
    service,
    widget,
)
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


_TOKEN_PATHS = ("/ics/", "/widget/", "/mobile/")


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


_access_logger = logging.getLogger("uvicorn.access")
# 先挂新的再摘旧的（热重载时）：脱敏不留空档，改过的脱敏规则也能立刻生效
_old_redact = [f for f in _access_logger.filters if type(f).__name__ == "_RedactTokenPaths"]
_access_logger.addFilter(_RedactTokenPaths())
for _f in _old_redact:
    _access_logger.removeFilter(_f)
admin.install_log_capture()


_upstream: ThreadPoolExecutor = (
    globals().get("_upstream") or ThreadPoolExecutor(max_workers=MAX_CONCURRENCY, thread_name_prefix="upstream")
)


async def _upstream_call(fn):
    """学校 I/O 放在专用线程池，避免占满 Starlette 默认线程池，把本地读接口堵住。"""
    global _upstream
    if getattr(_upstream, "_shutdown", False):
        _upstream = ThreadPoolExecutor(max_workers=MAX_CONCURRENCY, thread_name_prefix="upstream")
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
    reloader.install_signal_handler()
    reloader.start_watcher()
    try:
        yield
    finally:
        reloader.stop_watcher()
        scheduler.stop()
        mailer.stop()
        _upstream.shutdown(wait=False, cancel_futures=True)


class ReloadableApp:
    """ASGI 热重载网关：对外保持稳定的 app 引用，热重载时原子切换内部 FastAPI 实例。"""

    def __init__(self, target: FastAPI):
        self._target = target

    def set_target(self, target: FastAPI) -> None:
        self._target = target

    async def __call__(self, scope, receive, send) -> None:
        await self._target(scope, receive, send)

    def __getattr__(self, name: str):
        return getattr(self._target, name)


_gateway: ReloadableApp | None = globals().get("_gateway")
_fastapi_app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app: FastAPI = _fastapi_app
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
    """小组件交回代抓结果 / 要日历订阅链接、安卓 App 配对：客户端不带 Origin，凭路径里的 token 认人、不用 cookie，
    没有 CSRF 的问题。浏览器带了别站的 Origin 仍然拒绝。"""
    path = request.url.path
    return (request.method == "POST" and not request.headers.get("origin")
            and ((path.startswith("/widget/") and path.endswith(("/upload", "/calendar")))
                 or (path.startswith("/mobile/") and path.endswith(("/pair", "/upload", "/leave", "/push", "/forget-session")))
                 or path in _APP_POSTS))


# 安卓 App 的匿名心跳与协助刷新：不用 cookie，凭随机安装 id / 会话 token 认人，同样没有 CSRF 的问题
_APP_POSTS = frozenset({"/api/app/heartbeat", "/api/app/enroll", "/api/assist/claim", "/api/assist/exchange"})


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
                                   and not request.url.path.startswith(("/api/admin/", "/api/app/", "/api/assist/"))):
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


def _ics_url(uid: int, row=None) -> str | None:
    if row is None:
        row = db.connect().execute("SELECT enc_ics_token FROM users WHERE id=?", (uid,)).fetchone()
    if not row or not row["enc_ics_token"]:
        return None
    try:
        token = crypto.decrypt(row["enc_ics_token"], "ics", uid).decode()
    except (crypto.CryptoError, UnicodeDecodeError):
        log.warning("user %s: ics token cannot be decrypted", uid)
        return None
    return f"{PUBLIC_BASE_URL}/ics/{token}.ics"


def _widget_token(uid: int, row=None) -> str | None:
    if row is None:
        row = db.connect().execute("SELECT enc_widget_token FROM users WHERE id=?", (uid,)).fetchone()
    if not row or not row["enc_widget_token"]:
        return None
    try:
        return crypto.decrypt(row["enc_widget_token"], "widget", uid).decode()
    except (crypto.CryptoError, UnicodeDecodeError):
        log.warning("user %s: widget token cannot be decrypted", uid)
        return None


def _widget_links(token: str | None) -> dict:
    """同一个 token 两条路径：/widget/ 给 iOS 脚本，/mobile/ 给安卓 App。"""
    return {
        "widget_url": f"{PUBLIC_BASE_URL}/widget/{token}.json" if token else None,
        "mobile_url": f"{PUBLIC_BASE_URL}/mobile/{token}.json" if token else None,
    }


def _touch_use(uid: int) -> None:
    """记下“还在用”。纯本地数据库操作，出错只记日志，绝不影响请求本身。"""
    try:
        service.touch(uid)
    except Exception as e:
        log.warning("record use failed for user %s: %s", uid, type(e).__name__)


def _queue_if_stale(uid: int, row=None) -> bool:
    """数据比目标间隔还旧就排一次后台刷新，不访问学校。row 必须是 touch 之后读的：
    目标间隔取决于 last_seen_at，用旧值会把刚回来的人当成还在沉寂。返回是否在排队。"""
    try:
        return service.request_if_stale(uid, row=row)
    except Exception as e:
        log.warning("record use failed for user %s: %s", uid, type(e).__name__)
        return False


def _record_use(uid: int) -> None:
    """有人在用（网站 / 小组件 / 日历）：更新活跃度，必要时排一次后台刷新。"""
    _touch_use(uid)
    _queue_if_stale(uid)


def _json_list(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


@app.get("/api/me")
def me(request: Request, response: Response, sess=Depends(current_session)):
    # 每次打开页面都会先调这里：顺手做会话滑动续期，记下“还在用”，数据过期就排一次后台刷新
    uid = sess["id"]
    renew_session(request, response, sess)
    _touch_use(uid)
    row = db.connect().execute(
        f"SELECT status, next_refresh_at, enc_ics_token, enc_widget_token, widget_fetch, "
        f"{service._SIGNAL_COLS} FROM users WHERE id=?",
        (uid,)).fetchone()
    queued = _queue_if_stale(uid, row=row)
    outlook = service.refresh_outlook(uid, row=None if queued else row)
    if device_hint_uid(request) != uid:
        set_device_hint(response, uid)  # 已登录的老会话也补上，下次扫码即可复用设备
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
        "ics_url": _ics_url(uid, row=row),
        "mail": notify.snapshot(uid),
        **_widget_links(_widget_token(uid, row=row)),
        "widget_fetch": bool(row and row["widget_fetch"]),
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
_script_cache: tuple[int, str, str] | None = globals().get("_script_cache")


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


mobile_pair_by_user = RateLimiter(20, 900, prev=globals().get("mobile_pair_by_user"))


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
        mobile_devices.clear(c, sess["id"])
    return _widget_links(token)


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
        mobile_devices.clear(c, sess["id"])
    return {"ok": True}


@app.get("/api/widget/script")
def widget_script(sess=Depends(current_session)):
    """把本人的只读链接填进 Scriptable 脚本模板，设置页一键复制。"""
    token = _widget_token(sess["id"])
    url = _widget_links(token)["widget_url"]
    if not url or not token:
        raise HTTPException(404, "请先生成小组件链接")
    template, _version = _widget_template()
    script = template.replace("__WIDGET_URL__", url).replace("__WIDGET_FETCH_KEY__", crypto.widget_fetch_key(token))
    return PlainTextResponse(script, media_type="text/javascript; charset=utf-8", headers={"Cache-Control": "no-store"})


@app.get("/widget/{token}.js")
def widget_script_by_token(token: str):
    """脚本在 Scriptable 里自我更新用：凭同一个只读 token 取最新脚本（已填好本人链接）。
    不填代抓密钥：只有链接的人不能从这里拿到它，脚本更新时自己把手里的密钥带过去。"""
    _widget_user(token)
    template, _version = _widget_template()
    return PlainTextResponse(template.replace("__WIDGET_URL__", f"{PUBLIC_BASE_URL}/widget/{token}.json"),
                             media_type="text/javascript; charset=utf-8",
                             headers={"Cache-Control": "no-store", **_WIDGET_HEADERS})


@app.get("/widget/{token}.json")
def widget_feed(token: str, request: Request, refresh: int = Query(0, ge=0, le=1)):
    user = _widget_user(token)
    sent = request.headers.get("x-widget-key", "").encode()
    keyed = hmac.compare_digest(sent, crypto.widget_fetch_key(token).encode())
    return _feed(user, refresh, "widget", delegate=keyed)


@app.get("/mobile/{token}.json")
def mobile_feed(token: str, request: Request, refresh: int = Query(0, ge=0, le=1)):
    user = _widget_user(token)
    return _feed(user, refresh, "mobile", delegate=_device_signed(user["id"], token, request))


_EMPTY_BODY_SHA256 = hashlib.sha256(b"").hexdigest()


def _device_signed(uid: int, token: str, request: Request) -> bool:
    h = request.headers
    nonce = h.get("x-device-nonce", "")
    try:
        mobile_devices.authenticate(
            uid, token, h.get("x-device-id", ""), h.get("x-device-time", ""),
            h.get("x-device-signature", ""), method=request.method, path=request.url.path,
            body_hash=_EMPTY_BODY_SHA256, nonce=nonce,
        )
    except mobile_devices.AuthError:
        return False
    return True


def _feed(user, refresh: int, via: str, delegate: bool):
    presence.mark_user(user["id"], via)
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
    task = None
    # 手机代抓：Scriptable 脚本要带代抓密钥，安卓 App 要绑定过的设备签名。
    # 只拿到 /widget 或 /mobile 链接的人拿不到任务里的教务 cookie，这时由服务器刷
    if via == "widget" and not delegate:
        # 没带密钥的多半是升级前装的脚本：提示用户重新复制，否则这台手机再也不会代抓
        payload["fetch_key_required"] = _widget_fetch_enabled(user["id"])
    if delegate:
        try:
            task = service.widget_fetch_task(user["id"], pending=bool(payload["refresh"].get("pending")))
        except Exception as e:
            log.warning("widget fetch task failed for user %s: %s", user["id"], type(e).__name__)
        if task:
            payload["fetch"] = task
        payload["delegate"] = _widget_fetch_enabled(user["id"])
        payload["poll_after"] = service.widget_poll_after(user["id"])
    # 正在排队 / 带着代抓任务（含 cookie）时不要缓存，两分钟后的重画必须打到服务器
    caching = "private, no-store" if payload["refresh"].get("pending") or task else "private, max-age=60"
    return JSONResponse(payload, headers={"Cache-Control": caching, **_WIDGET_HEADERS})


class MobilePairIn(BaseModel):
    code: str = Field(max_length=128)
    public_key: str = Field(max_length=512)
    name: str = Field("", max_length=128)


@app.post("/api/mobile/pair")
def mobile_pair_start(sess=Depends(csrf_protected)):
    """网站发一次性配对码，和链接一起放进 nufekebiao:// 交给 App。"""
    url = _widget_links(_widget_token(sess["id"]))["mobile_url"]
    if not url:
        raise HTTPException(404, "请先生成链接")
    code = mobile_devices.start_pairing(sess["id"])
    return JSONResponse({"pair_url": f"nufekebiao://setup?url={quote(url, safe='')}&pair={code}",
                         "expires_in": mobile_devices.PAIR_TTL}, headers={"Cache-Control": "no-store"})


@app.get("/api/mobile/devices")
def mobile_device_list(sess=Depends(current_session)):
    return {"devices": mobile_devices.list_devices(sess["id"])}


@app.post("/api/mobile/devices/{device_id}/revoke")
def mobile_device_revoke(device_id: str, sess=Depends(csrf_protected)):
    if not mobile_devices.revoke(sess["id"], device_id):
        raise HTTPException(404, "设备不存在")
    return {"ok": True}


@app.post("/mobile/{token}/pair")
def mobile_pair(token: str, body: MobilePairIn):
    user = _widget_user(token)
    if not mobile_pair_by_user.hit(str(user["id"])):
        raise HTTPException(429, "尝试太频繁，请稍后再试")
    try:
        device_id = mobile_devices.bind(user["id"], body.code, body.public_key, body.name)
    except mobile_devices.PairError as e:
        raise HTTPException(400, str(e)) from e
    device = next(d for d in mobile_devices.list_devices(user["id"]) if d["id"] == device_id)
    notify.device_bound(user["id"], device["name"])
    return JSONResponse({"device_id": device_id}, headers={"Cache-Control": "no-store", **_WIDGET_HEADERS})


@app.get("/mobile/{token}/profile")
def mobile_profile(token: str, request: Request):
    """个人信息：只给配对过的设备，要带设备私钥的签名。链接泄露时这里拿不到东西。"""
    user = _widget_user(token)
    h = request.headers
    nonce = h.get("x-device-nonce", "")
    try:
        mobile_devices.authenticate(
            user["id"], token, h.get("x-device-id", ""), h.get("x-device-time", ""),
            h.get("x-device-signature", ""), method=request.method, path=request.url.path,
            body_hash=_EMPTY_BODY_SHA256, nonce=nonce,
        )
    except mobile_devices.AuthError as e:
        body = {"detail": "设备未绑定或签名无效"}
        if e.skew:
            body["server_time"] = int(db.now())
        return JSONResponse(body, status_code=401, headers={"Cache-Control": "no-store", **_WIDGET_HEADERS})
    student = (service.timetable_view(user["id"], None) or {}).get("student") or {}
    email = notify.snapshot(user["id"]).get("email")
    return JSONResponse({
        "student": {k: student.get(k) for k in ("name", "code", "department", "major", "adminclass", "grade")},
        "mail": {"bound": bool(email), "email": mailer.mask(email) if email else None},
    }, headers={"Cache-Control": "no-store", **_WIDGET_HEADERS})


def _widget_payload(user) -> dict:
    view = service.timetable_view(user["id"], None)
    _template, version = _widget_template()
    payload = widget.build(view, view.get("week1_monday") if view else None, status=user["status"],
                           script_version=version, changes=service.recent_changes(user["id"]))
    payload["refresh"] = service.widget_refresh_state(user["id"])
    payload["delegate"] = _widget_fetch_enabled(user["id"])
    payload["poll_after"] = service.widget_poll_after(user["id"])
    return payload


WIDGET_UPLOAD_MAX = 4 * 1024 * 1024


@app.post("/widget/{token}/upload")
@app.post("/mobile/{token}/upload")
async def widget_upload(token: str, request: Request):
    """小组件用手机网络抓到的课表页和 print-data。响应带上最新的小组件数据，脚本同一次运行就能画上。"""
    user = await asyncio.to_thread(_widget_user, token)
    size = request.headers.get("content-length") or "0"
    if not size.isdigit() or int(size) > WIDGET_UPLOAD_MAX:
        raise HTTPException(413, "上传内容过大")
    if not await asyncio.to_thread(service.has_active_widget_task, user["id"]):
        raise HTTPException(400, "任务不存在")
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > WIDGET_UPLOAD_MAX:
            raise HTTPException(413, "上传内容过大")

    def run():
        try:
            body = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "格式不正确") from None
        if not isinstance(body, dict):
            raise HTTPException(400, "格式不正确")
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

_asset_cache: tuple[tuple, str] | None = globals().get("_asset_cache")


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

admin_login_by_ip = RateLimiter(5, 900, prev=globals().get("admin_login_by_ip"))


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
def admin_refreshes(trigger: str | None = Query(None, pattern="^(widget|assist|device|scheduled|manual|login)$"),
                    failed: bool = False, limit: int = Query(100, ge=1, le=500),
                    _csrf=Depends(admin.current_admin)):
    return admin.recent_refreshes(limit, trigger, failed)


@app.get("/api/admin/android")
def admin_android(_csrf=Depends(admin.current_admin)):
    return admin.android()


class AdminRefreshIn(BaseModel):
    uid: int = Field(ge=1)


@app.post("/api/admin/request-refresh")
def admin_request_refresh(body: AdminRefreshIn, _csrf=Depends(admin.admin_csrf)):
    """把这个人排到队首（仍受全站预算约束，不会绕过对学校的限速）。"""
    service.request_refresh(body.uid, "管理员手动排队")
    return {"ok": True}


@app.post("/api/admin/reload")
def admin_reload(_csrf=Depends(admin.admin_csrf)):
    """管理员手动触发进程内热重载。"""
    try:
        return {"ok": True, **reloader.reload(trigger="admin")}
    except reloader.ReloadError as e:
        raise HTTPException(400, str(e)) from None


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


# ---------- 本地优先安卓 App：匿名统计与协助刷新（见 assist.py） ----------

app_heartbeat_by_ip = RateLimiter(60, 3600, prev=globals().get("app_heartbeat_by_ip"))
app_new_install_by_ip = RateLimiter(10, 86400, prev=globals().get("app_new_install_by_ip"))
assist_claim_by_install = RateLimiter(20, 3600, prev=globals().get("assist_claim_by_install"))
assist_claim_by_ip = RateLimiter(60, 3600, prev=globals().get("assist_claim_by_ip"))
ASSIST_EXCHANGE_MAX = 1024 * 1024


class AppHeartbeatIn(BaseModel):
    install_id: str = Field(min_length=16, max_length=64)
    version: str | None = Field(None, max_length=40)
    version_code: int | None = Field(None, ge=0, le=10_000_000)
    sdk: int | None = Field(None, ge=1, le=1000)
    has_timetable: bool = False
    assist: bool = False


@app.post("/api/app/heartbeat")
def app_heartbeat(body: AppHeartbeatIn, request: Request):
    """App 的匿名心跳：随机安装 id（只存哈希）+ 版本。不收学号、姓名、课表。"""
    if not assist.valid_install_id(body.install_id):
        raise HTTPException(400, "安装 id 格式不正确")
    ip = _client_ip(request)
    if not app_heartbeat_by_ip.hit(ip):
        raise HTTPException(429, "请求过于频繁")
    recorded = assist.heartbeat(body.install_id, body.version, body.version_code, body.sdk,
                                body.has_timetable, body.assist, allow_new=False)
    if not recorded and app_new_install_by_ip.hit(ip):
        recorded = assist.heartbeat(body.install_id, body.version, body.version_code, body.sdk,
                                    body.has_timetable, body.assist)
    return {"ok": recorded, "assist_available": assist.ENABLED}


class AssistClaimIn(BaseModel):
    install_id: str = Field(min_length=16, max_length=64)


@app.post("/api/assist/claim")
def assist_claim(body: AssistClaimIn, request: Request):
    if not assist.valid_install_id(body.install_id):
        raise HTTPException(400, "安装 id 格式不正确")
    if not assist_claim_by_ip.hit(_client_ip(request)) or not assist_claim_by_install.hit(body.install_id):
        return {"task": None, "reason": "rate_limited"}
    return assist.claim(body.install_id)


@app.post("/api/assist/exchange")
async def assist_exchange(request: Request):
    """中继搬运：手机交上从学校收到的密文，取走要发给学校的密文。最多挂起 assist.LONG_POLL 秒。"""
    size = request.headers.get("content-length") or "0"
    if not size.isdigit() or int(size) > ASSIST_EXCHANGE_MAX:
        raise HTTPException(413, "内容过大")
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > ASSIST_EXCHANGE_MAX:
            raise HTTPException(413, "内容过大")
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "格式不正确") from None
    if (not isinstance(body, dict) or not isinstance(body.get("session"), str)
            or not isinstance(body.get("token"), str) or not isinstance(body.get("events", []), list)):
        raise HTTPException(400, "格式不正确")
    events = [e for e in body.get("events", [])[:64] if isinstance(e, dict)]
    out = await asyncio.to_thread(assist.exchange, body["session"][:40], body["token"][:64], events)
    if out is None:
        raise HTTPException(404, "会话不存在")
    return out


app_enroll_by_ip = RateLimiter(10, 3600, prev=globals().get("app_enroll_by_ip"))


class AppEnrollIn(BaseModel):
    cookies: list[dict] = Field(max_length=service.APP_COOKIE_MAX)
    public_key: str = Field(max_length=512)
    name: str = Field("", max_length=128)
    keep_session: bool = True   # False：只把手机当 iOS 小组件 / 日历的数据源，本站不留学校登录态


def _ensure_widget_token(uid: int) -> str:
    """托管需要一个只读链接作为设备的“门牌”。已有就沿用（重新生成会让用户别的小组件 / 设备失效）。"""
    token = _widget_token(uid)
    if token:
        return token
    token = secrets.token_urlsafe(24)
    with db.tx() as c:
        c.execute("UPDATE users SET widget_token_hash=?, enc_widget_token=? WHERE id=? AND enc_widget_token IS NULL",
                  (crypto.token_hash(token), crypto.encrypt(token.encode(), "widget", uid), uid))
    return _widget_token(uid) or token


@app.post("/api/app/enroll")
async def app_enroll(body: AppEnrollIn, request: Request):
    """App 2.x 登录时勾了“托管到本站”：凭刚拿到的学校会话建号 / 换登录态，并直接绑定这台设备。

    双层兜底：手机本机刷新失败时读本站这份；本站的登录态失效时，App 下次本机刷新成功会再交一次。
    """
    cookies = service.app_cookies(body.cookies)
    if not any(c["domain"] == "jwxt.nufe.edu.cn" for c in cookies) and not any(c["name"] == "CASTGC" for c in cookies):
        raise HTTPException(400, "没有可用的学校登录状态")
    if not app_enroll_by_ip.hit(_client_ip(request)):
        raise HTTPException(429, "尝试太频繁，请稍后再试")
    try:
        mobile_devices._public_key_der(body.public_key)
    except mobile_devices.PairError as e:
        raise HTTPException(400, str(e)) from e

    def run():
        try:
            return service.enroll_from_app(cookies, keep_session=body.keep_session)
        except service.LoginRejected as e:
            raise HTTPException(400, str(e)) from None
        except service.Busy:
            raise HTTPException(409, "正在刷新，请稍后再试") from None

    uid = await _upstream_call(run)
    token = await asyncio.to_thread(_ensure_widget_token, uid)
    device_id = await asyncio.to_thread(mobile_devices.bind_verified, uid, body.public_key, body.name)
    await asyncio.to_thread(notify.device_bound, uid, body.name.strip()[:64] or "安卓设备")
    row = db.connect().execute("SELECT student_id, name FROM users WHERE id=?", (uid,)).fetchone()
    return JSONResponse({"token": token, "device_id": device_id,
                         "student": {"code": row["student_id"], "name": row["name"]}},
                        headers={"Cache-Control": "no-store", **_WIDGET_HEADERS})


def _signed_device(token: str, request: Request, body_hash: str | None = None):
    """/mobile/{token}/… 里要带设备签名的接口：返回 (user, None) 或 (None, 401 响应)。"""
    user = _widget_user(token)
    h = request.headers
    nonce = h.get("x-device-nonce", "")
    bh = body_hash if body_hash is not None else _EMPTY_BODY_SHA256
    try:
        mobile_devices.authenticate(
            user["id"], token, h.get("x-device-id", ""), h.get("x-device-time", ""),
            h.get("x-device-signature", ""), method=request.method, path=request.url.path,
            body_hash=bh, nonce=nonce,
        )
    except mobile_devices.AuthError as e:
        body = {"detail": "设备未绑定或签名无效"}
        if e.skew:
            body["server_time"] = int(db.now())
        return None, JSONResponse(body, status_code=401, headers={"Cache-Control": "no-store", **_WIDGET_HEADERS})
    return user, None


@app.get("/mobile/{token}/timetable")
def mobile_timetable(token: str, request: Request):
    """托管的 App 读本站这份课表（原始排课，App 本机再投影）。也算“还在用”，到期会被排进后台刷新。"""
    user, denied = _signed_device(token, request)
    if denied:
        return denied
    presence.mark_user(user["id"], "mobile")
    _record_use(user["id"])
    return JSONResponse(service.app_timetable(user["id"]), headers={"Cache-Control": "no-store", **_WIDGET_HEADERS})


DEVICE_PUSH_MAX = 1024 * 1024


@app.post("/mobile/{token}/push")
async def mobile_push(token: str, request: Request):
    """设备数据源：手机把本机刷到的课表推上来，iOS 小组件（/widget/…）和日历订阅（/ics/…）就读这份。"""
    size = request.headers.get("content-length") or "0"
    if not size.isdigit() or int(size) > DEVICE_PUSH_MAX:
        raise HTTPException(413, "内容过大")
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > DEVICE_PUSH_MAX:
            raise HTTPException(413, "内容过大")
    body_hash = hashlib.sha256(bytes(raw)).hexdigest()
    user, denied = await asyncio.to_thread(_signed_device, token, request, body_hash)
    if denied:
        return denied
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "格式不正确") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "格式不正确")

    def run():
        try:
            return service.accept_device_push(user["id"], body)
        except service.DevicePushRejected as e:
            log.info("device push rejected for user %s: %s", user["id"], e)
            raise HTTPException(400, str(e)) from None
        except service.UserGone:
            raise HTTPException(404) from None

    out = await asyncio.to_thread(run)
    return JSONResponse(out, headers={"Cache-Control": "no-store", **_WIDGET_HEADERS})


def _ensure_ics_token(uid: int) -> str | None:
    """日历订阅链接：已有就沿用，没有就生成一个（和网站设置页的“生成订阅链接”同一个 token）。"""
    url = _ics_url(uid)
    if url:
        return url
    token = secrets.token_urlsafe(24)
    with db.tx() as c:
        c.execute("UPDATE users SET ics_token_hash=?, enc_ics_token=? WHERE id=? AND enc_ics_token IS NULL",
                  (crypto.token_hash(token), crypto.encrypt(token.encode(), "ics", uid), uid))
    return _ics_url(uid)


@app.get("/mobile/{token}/links")
def mobile_links(token: str, request: Request):
    """给 App 展示的 iOS 小组件与日历订阅链接（只给签名过的本人设备）。"""
    user, denied = _signed_device(token, request)
    if denied:
        return denied
    ics = _ensure_ics_token(user["id"])
    row = db.connect().execute("SELECT cred_mode, enc_cookies IS NOT NULL AS hosted, last_success_at FROM users WHERE id=?",
                               (user["id"],)).fetchone()
    return JSONResponse({
        "widget_url": f"{PUBLIC_BASE_URL}/widget/{token}.json",
        "script_url": f"{PUBLIC_BASE_URL}/widget/{token}.js",
        "ics_url": ics,
        "webcal_url": ics.replace("https://", "webcal://", 1).replace("http://", "webcal://", 1) if ics else None,
        "server_refresh": bool(row["hosted"]) or row["cred_mode"] == "password",
        "last_success_at": row["last_success_at"],
    }, headers={"Cache-Control": "no-store", **_WIDGET_HEADERS})


@app.post("/mobile/{token}/forget-session")
def mobile_forget_session(token: str, request: Request):
    """关掉服务器托管但保留数据源：删本站的学校登录态，设备与链接都保留。"""
    user, denied = _signed_device(token, request)
    if denied:
        return denied
    service.forget_session_from_app(user["id"])
    return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store", **_WIDGET_HEADERS})


@app.post("/mobile/{token}/leave")
def mobile_leave(token: str, request: Request):
    user, denied = _signed_device(token, request)
    if denied:
        return denied
    service.leave_from_app(user["id"], request.headers.get("x-device-id", ""))
    return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store", **_WIDGET_HEADERS})


@app.get("/android/releases.json")
def android_release_feed():
    """给连不上 GitHub 的 App 查新版本：不需要登录，也不含任何个人信息。"""
    return JSONResponse(android_releases.feed(), headers={"Cache-Control": "public, max-age=300"})


@app.get("/android/latest.apk")
def android_latest_apk():
    tag = android_releases.latest_stable_tag()
    if not tag:
        raise HTTPException(404, "暂时没有可下载的版本")
    return RedirectResponse(f"apk/{tag}.apk", 302)


@app.get("/android/apk/{tag}.apk")
def android_apk(tag: str):
    try:
        found = android_releases.apk_path(tag)
    except android_releases.MirrorError as e:
        raise HTTPException(502, str(e)) from e
    if not found:
        raise HTTPException(404)
    path, name = found
    # 安装包本来就压缩过：标明 identity，GZip 中间件不再白压一遍
    return FileResponse(path, media_type="application/vnd.android.package-archive", filename=name,
                        headers={"Cache-Control": "public, max-age=86400", "Content-Encoding": "identity"})


@app.get("/health")
@app.get("/healthz")
def healthz():
    return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")

if _gateway is None:
    _gateway = ReloadableApp(_fastapi_app)
else:
    _gateway.__class__ = ReloadableApp  # 网关对象要保持不变（uvicorn 握着它），但用上新版的类
    _gateway.set_target(_fastapi_app)
app = _gateway
