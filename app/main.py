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

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import crypto, db, scheduler, service
from .config import (FORCE_HTTPS, MAX_USER_REFRESH_TIMES, PUBLIC_BASE_URL, REFRESH_ALLOWED_HOURS,
                     REFRESH_INTERVAL_HOURS, ROOT, SCHEDULER_ENABLED)
from .polite import MAX_CONCURRENCY
from .security import (clear_device_hint, clear_session_cookie, create_session, csrf_protected, current_session,
                       destroy_all_sessions, destroy_session, device_hint_uid, login_by_ip, login_by_student,
                       origin_ok, set_device_hint)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("urllib3").setLevel(logging.WARNING)


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
    yield
    scheduler.stop()
    _upstream.shutdown(wait=False, cancel_futures=True)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
STATIC = ROOT / "static"


@app.middleware("http")
async def security_mw(request: Request, call_next):
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
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'")
    if FORCE_HTTPS:
        # 不加 includeSubDomains：同域名下还有其他子站，不能替它们强制 HTTPS
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
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
        set_device_hint(response, r["uid"])
        return {"ok": True, "csrf": create_session(response, r["uid"], r.get("keep_login", False))}
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
            return {"message": service.login_send_sms(body.flow_id)}
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
    token = crypto.decrypt(row["enc_ics_token"], "ics", uid).decode()
    return f"{PUBLIC_BASE_URL}/ics/{token}.ics"


@app.get("/api/me")
def me(request: Request, response: Response, sess=Depends(current_session)):
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
        "semesters": json.loads(sess["semesters_json"] or "[]"),
        "ics_url": _ics_url(sess["id"]),
        "refresh_hours": json.loads(sess["refresh_hours"] or "[]"),
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
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"ok": True, "next_refresh_at": nxt}


# ---------- 课表 ----------

@app.get("/api/timetable")
def timetable(semester_id: int | None = None, sess=Depends(current_session)):
    view = service.timetable_view(sess["id"], semester_id)
    if not view:
        raise HTTPException(404, "该学期尚未抓取")
    return view


class RefreshIn(BaseModel):
    semester_id: int | None = None


@app.post("/api/refresh")
async def refresh(body: RefreshIn, sess=Depends(csrf_protected)):
    uid = sess["id"]

    def run():
        try:
            result = service.refresh_user(uid, "manual", body.semester_id)
        except service.Busy:
            raise HTTPException(409, "正在刷新中") from None
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
        c.execute("UPDATE users SET ics_token_hash=?, enc_ics_token=? WHERE id=?",
                  (crypto.token_hash(token), crypto.encrypt(token.encode(), "ics", sess["id"]), sess["id"]))
    return {"ics_url": f"{PUBLIC_BASE_URL}/ics/{token}.ics"}


@app.post("/api/ics/disable")
def ics_disable(sess=Depends(csrf_protected)):
    with db.tx() as c:
        c.execute("UPDATE users SET ics_token_hash=NULL, enc_ics_token=NULL WHERE id=?", (sess["id"],))
    return {"ok": True}


@app.post("/api/account/delete")
def delete_account(request: Request, response: Response, sess=Depends(csrf_protected)):
    service.delete_user(sess["id"])
    clear_session_cookie(response)
    clear_device_hint(response)
    return {"ok": True}


@app.get("/ics/{token}.ics")
def ics_feed(token: str, request: Request):
    user = db.connect().execute(
        "SELECT id, student_id FROM users WHERE ics_token_hash=?", (crypto.token_hash(token),)).fetchone()
    if not user:
        raise HTTPException(404)
    max_fetched, body = service.ics_body(user["id"], user["student_id"])
    etag = f'W/"{user["id"]}-{max_fetched}"'
    headers = {"Cache-Control": "private, max-age=3600", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return PlainTextResponse(body, media_type="text/calendar; charset=utf-8", headers=headers)


# ---------- 前端 ----------

def _asset_version() -> str:
    h = hashlib.sha256()
    for name in ("app.css", "app.js"):
        h.update((STATIC / name).read_bytes())
    return h.hexdigest()[:12]


_INDEX_HTML = (STATIC / "index.html").read_text("utf-8").replace("{{v}}", _asset_version())


@app.get("/")
def index():
    # 静态资源 URL 带内容哈希，部署后浏览器立刻拿到新 JS/CSS；页面本身不缓存
    return HTMLResponse(_INDEX_HTML, headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
