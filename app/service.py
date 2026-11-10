"""业务核心：代登录、抓取、凭据保管、刷新状态机。

凭据策略：
- 默认 cookie 模式：只保存（加密的）CAS TGC + 教务会话 cookie，不保存密码。
  TGC 过期后无法后台续期，标记 needs_rebind，请用户重新登录一次。
- password 模式（用户明确勾选同意后）：额外保存加密的教务密码，登录态过期时自动重登。
  即便如此，刷新也总是先尝试 cookie，只有 cookie 失效才解密密码。
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import logging
import math
import random
import re
import secrets
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import requests

from . import cas, crypto, db, ics, jwxt, notify, refresh_policy, widget
from .config import (
    MANUAL_REFRESH_FRESH_SECONDS,
    MANUAL_REFRESH_MIN_GAP,
    PASSWORD_STORAGE_ENABLED,
    REFRESH_MAX_FAILS,
    SSO_KEEPALIVE_IDLE_DAYS,
    SSO_KEEPALIVE_MINUTES,
    TIMEZONE,
    semester_overrides,
)
from .polite import PoliteSession, RelayFailed, UpstreamCooldown, UpstreamYield, dump_cookies, load_cookies

log = logging.getLogger("kebiao.service")

_locks: defaultdict[int, threading.Lock] = globals().get("_locks") or defaultdict(threading.Lock)


class NeedsRebind(Exception):
    pass


class Busy(Exception):
    pass


class UserGone(Exception):
    """刷新过程中账号被删除（或数据已不存在）。"""


class TooSoon(Exception):
    """手动刷新点得太密：还要等 retry_after 秒。"""

    def __init__(self, retry_after: int):
        super().__init__(retry_after)
        self.retry_after = retry_after


class UpstreamDown(Exception):
    """学校服务暂时不可用：按普通失败退避重试，而不是让用户重新登录。"""


class LoginRejected(Exception):
    """站点登录失败（给前端看的消息）。"""


# ---------- 登录 / 绑定 ----------
#
# 登录可能要求真人填写图形验证码或短信验证码，所以拆成多步：
# 未完成的登录流程（含 requests 会话，以及仅在用户选择保存时的密码）只放在内存里，5 分钟过期，不落库。

PENDING_TTL = 300
MAX_PENDING_FLOWS = 200
MAX_VERIFY_ATTEMPTS = 5
MAX_SMS_SENDS = 3
MAX_QR_REFRESHES = 10
MAX_CAPTCHA_IMAGES = 8
LOG_KEEP = 50


@dataclass
class PendingLogin:
    flow_id: str
    flow: cas.LoginFlow
    password_to_store: str | None
    expires_at: float
    lock: threading.Lock = field(default_factory=threading.Lock)
    verify_attempts: int = 0
    sms_sends: int = 0
    keep_login: bool = False       # 本站会话保持 180 天
    keep_creds: bool = False       # 扫码登录：不改动已保存的凭据设置
    qr_polls: int = 0
    qr_refreshes: int = 0
    captcha_fetches: int = 0
    poll_lock: threading.Lock = field(default_factory=threading.Lock)


_pending: dict[str, PendingLogin] = globals().get("_pending") if "_pending" in globals() else {}
_pending_lock: threading.RLock = globals().get("_pending_lock") or threading.RLock()
# 后台轮询完成登录后，下一次 HTTP poll 从这里取结果（flow_id → (到期时间, 返回值)）
_completed: dict[str, tuple[float, dict]] = globals().get("_completed") if "_completed" in globals() else {}
_ics_cache: dict[int, tuple[tuple, str, str]] = globals().get("_ics_cache") if "_ics_cache" in globals() else {}


def _purge_pending() -> None:
    now = db.now()
    with _pending_lock:
        for k in [k for k, p in _pending.items() if p.expires_at < now]:
            p = _pending.pop(k)
            p.flow.wipe_password()
            p.password_to_store = None
        for k in [k for k, (exp, _) in _completed.items() if exp < now]:
            _completed.pop(k, None)


def _track(p: PendingLogin) -> None:
    with _pending_lock:
        if len(_pending) >= MAX_PENDING_FLOWS and p.flow_id not in _pending:
            _purge_pending()
            while len(_pending) >= MAX_PENDING_FLOWS:
                oldest_id = min(_pending, key=lambda k: _pending[k].expires_at)
                evicted = _pending.pop(oldest_id)
                evicted.flow.wipe_password()
                evicted.password_to_store = None
        _pending[p.flow_id] = p


def _drop(p: PendingLogin) -> None:
    p.flow.wipe_password()
    p.password_to_store = None
    with _pending_lock:
        _pending.pop(p.flow_id, None)


def _get_pending(flow_id: str) -> PendingLogin:
    _purge_pending()
    p = _pending.get(flow_id or "")
    if not p:
        raise LoginRejected("登录流程已过期，请重新开始登录")
    return p


def login_start(student_id: str, password: str, remember_password: bool, keep_login: bool = False,
                hint_uid: int | None = None) -> dict:
    """返回 {"uid": ...}（完成）或 {"need": "captcha"|"mfa", "flow_id": ..., ...}（需要用户继续）。

    只有这个浏览器的 kb_dev 指向同一用户时才复用已信任的设备指纹。
    否则学校会把这次登录当成新设备（密码对了也仍可能要短信）。
    """
    _purge_pending()
    p = PendingLogin(
        flow_id=secrets.token_urlsafe(24),
        flow=cas.LoginFlow(PoliteSession(), student_id, password, device_id=_device_for_login(student_id, hint_uid)),
        password_to_store=password if (remember_password and PASSWORD_STORAGE_ENABLED) else None,
        expires_at=db.now() + PENDING_TTL,
        keep_login=keep_login,
    )
    _track(p)
    with p.lock:
        return _advance(p, p.flow.start)


# 扫码登录：前端约每 2 秒轮询一次，单个流程最多轮询这么多次（≈二维码有效期），防止无限打学校接口
MAX_QR_POLLS = 120


def login_qr_start(keep_login: bool = False, hint_uid: int | None = None) -> dict:
    """生成学校的登录二维码。扫码前不知道是谁，学号在登录成功后从教务数据里取。

    hint_uid：这个浏览器上次登录的用户。复用他的设备 ID，学校就会认出是已信任的设备。
    """
    _purge_pending()
    p = PendingLogin(
        flow_id=secrets.token_urlsafe(24),
        flow=cas.LoginFlow(PoliteSession(), "", "", device_id=_device_id_for_uid(hint_uid)),
        password_to_store=None,
        expires_at=db.now() + PENDING_TTL,
        keep_login=keep_login,
        keep_creds=True,
    )
    _track(p)
    with p.lock:
        return _advance(p, p.flow.start_qr)


def login_qr_image(flow_id: str) -> tuple[bytes, str]:
    p = _get_pending(flow_id)
    with p.lock:
        if p.flow.stage != "qr":
            raise LoginRejected("当前不在扫码登录步骤")
        try:
            return p.flow.qr_image()
        except cas.CasError as e:
            raise LoginRejected(str(e)) from None


def login_qr_poll(flow_id: str) -> dict:
    """只读内存。学校的 getStatus 由调度线程在上游节流下轮询。"""
    _purge_pending()
    with _pending_lock:
        done = _completed.pop(flow_id, None)
    if done:
        return done[1]
    p = _get_pending(flow_id)
    if p.flow.stage not in _USER_STEPS:
        # 手机已确认，调度线程正拿着流程读课表、建号（这里不等 p.lock）：让浏览器接着轮询，几秒后就能拿到结果
        return {"need": "qr", "flow_id": p.flow_id, "error": None, "qr_status": "confirmed"}
    with p.poll_lock:
        p.qr_polls += 1
        over = p.qr_polls > MAX_QR_POLLS
    if over:
        p.flow.qr_status = "expired"
    return _need(p)


def login_qr_refresh(flow_id: str) -> dict:
    p = _get_pending(flow_id)
    with p.lock:
        if p.qr_refreshes >= MAX_QR_REFRESHES:
            raise LoginRejected("二维码刷新次数过多，请重新开始登录")
        p.qr_refreshes += 1
        with p.poll_lock:
            p.qr_polls = 0
        # 不延长 5 分钟流程 TTL，避免一个 flow 无限占着上游
        return _advance(p, p.flow.refresh_qr)


def login_submit_captcha(flow_id: str, captcha: str) -> dict:
    p = _get_pending(flow_id)
    with p.lock:
        if p.flow.stage != "captcha":
            raise LoginRejected("当前不需要图形验证码")
        _count_attempt(p)
        return _advance(p, lambda: p.flow.submit(captcha))


def login_captcha_image(flow_id: str) -> tuple[bytes, str]:
    p = _get_pending(flow_id)
    with p.lock:
        if p.flow.stage != "captcha":
            raise LoginRejected("当前不需要图形验证码")
        if p.captcha_fetches >= MAX_CAPTCHA_IMAGES:
            raise LoginRejected("验证码刷新次数过多，请重新登录")
        p.captcha_fetches += 1
        try:
            return p.flow.captcha_image()
        except cas.CasError as e:
            raise LoginRejected(str(e)) from None


def login_slider_challenge(flow_id: str) -> dict:
    """换一张滑块拼图（失败后、或用户点“换一张”）。与图形验证码共用刷新次数上限。"""
    p = _get_pending(flow_id)
    with p.lock:
        if p.flow.stage != "slider":
            raise LoginRejected("当前不需要滑块验证")
        if p.captcha_fetches >= MAX_CAPTCHA_IMAGES:
            raise LoginRejected("验证码刷新次数过多，请重新登录")
        p.captcha_fetches += 1
        try:
            return p.flow.slider_challenge()
        except cas.CasError as e:
            raise LoginRejected(str(e)) from None


def login_submit_slider(flow_id: str, canvas_length: int, move_length, tracks: list[dict]) -> dict:
    p = _get_pending(flow_id)
    with p.lock:
        if p.flow.stage != "slider":
            raise LoginRejected("当前不需要滑块验证")
        _count_attempt(p)
        return _advance(p, lambda: p.flow.submit_slider(canvas_length, move_length, tracks))


def login_cancel(flow_id: str) -> None:
    """用户主动取消：立刻释放流程（含内存中的密码），并丢弃已完成但未被取走的结果。"""
    with _pending_lock:
        p = _pending.pop(flow_id or "", None)
        _completed.pop(flow_id or "", None)
    if p:
        p.flow.wipe_password()
        p.password_to_store = None


def login_send_sms(flow_id: str) -> dict:
    p = _get_pending(flow_id)
    with p.lock:
        if p.sms_sends >= MAX_SMS_SENDS:
            raise LoginRejected("短信发送次数过多，请稍后重新登录")
        try:
            msg = p.flow.send_sms()
        except cas.CasError as e:
            raise LoginRejected(str(e)) from None
        p.sms_sends += 1
        retry_after = max(0, math.ceil(getattr(p.flow, "sms_ready_at", 0) - time.time()))
        return {"message": msg, "retry_after": retry_after}


def login_verify_sms(flow_id: str, code: str, trust_device: bool) -> dict:
    p = _get_pending(flow_id)
    with p.lock:
        if p.flow.stage != "mfa":
            raise LoginRejected("当前不需要二次认证")
        _count_attempt(p)
        return _advance(p, lambda: p.flow.verify_sms(code, trust_device))


def _count_attempt(p: PendingLogin) -> None:
    p.verify_attempts += 1
    if p.verify_attempts > MAX_VERIFY_ATTEMPTS:
        _drop(p)
        raise LoginRejected("验证失败次数过多，请重新登录")


def _need(p: PendingLogin, error: str | None = None) -> dict:
    with _pending_lock:
        _pending[p.flow_id] = p
    out = {"need": p.flow.stage, "flow_id": p.flow_id, "error": error}
    if p.flow.stage == "mfa":
        out["phone"] = p.flow.reauth.get("phone", "")
        out["method"] = p.flow.reauth.get("method", "sms")
        out["can_trust"] = p.flow.can_trust_device
    if p.flow.stage == "qr":
        out["qr_status"] = p.flow.qr_status
    return out


# 需要真人参与的步骤：出错时保留流程，让用户在原地重试
_USER_STEPS = ("captcha", "slider", "mfa", "qr")


def _advance(p: PendingLogin, step, stash: bool = False) -> dict:
    try:
        stage = step()
    except cas.MfaUnsupported as e:
        _drop(p)
        raise LoginRejected(str(e)) from None
    except cas.CasError as e:
        if p.flow.stage in _USER_STEPS:
            return _need(p, str(e))  # 验证码错了之类：留在当前步骤让用户重试
        _drop(p)
        raise LoginRejected(f"统一身份认证登录失败：{e}") from None
    except UpstreamCooldown as e:
        # 全站暂停访问学校期间：已经走到扫码 / 验证码 / 短信这一步的，保留流程让用户稍后继续
        msg = f"学校服务器繁忙，请约 {e.retry_after} 秒后再试"
        if p.flow.stage in _USER_STEPS:
            return _need(p, msg)
        _drop(p)
        raise LoginRejected(msg) from None
    except Exception:
        _drop(p)
        raise
    if stage != "done":
        return _need(p)
    try:
        result = {"uid": _complete_login(p), "keep_login": p.keep_login}
    except Exception:
        _drop(p)
        raise
    if stash:
        # 与移出 _pending 放在同一把锁里，HTTP poll 不会看到“两边都没有”
        with _pending_lock:
            while len(_completed) >= MAX_PENDING_FLOWS and p.flow_id not in _completed:
                oldest_id = min(_completed, key=lambda k: _completed[k][0])
                _completed.pop(oldest_id, None)
            _completed[p.flow_id] = (db.now() + 60, result)
            _pending.pop(p.flow_id, None)
        p.flow.wipe_password()
        p.password_to_store = None
    else:
        _drop(p)
    return result


def pump_qr_once() -> int:
    """对每个待扫码流程向上游查一次状态。由调度线程调用，受 PoliteSession 节流。"""
    _purge_pending()
    with _pending_lock:
        flows = [p for p in _pending.values() if p.flow.stage == "qr" and p.flow.qr_status != "expired"]
    n = 0
    for p in flows:
        if not p.lock.acquire(blocking=False):
            continue
        try:
            if p.flow.stage != "qr" or p.flow.qr_status == "expired":
                continue
            _advance(p, p.flow.poll_qr, stash=True)
            n += 1
        finally:
            p.lock.release()
    return n


def qr_waiting() -> bool:
    with _pending_lock:
        return any(p.flow.stage == "qr" and p.flow.qr_status != "expired" for p in _pending.values())


def _device_for_login(student_id: str, hint_uid: int | None) -> str:
    """kb_dev 解出的用户与该学号是同一人时，才复用已信任的设备指纹。"""
    row = db.connect().execute(
        "SELECT id, enc_device_id FROM users WHERE student_id=?", (student_id,)).fetchone()
    if row and hint_uid is not None and row["id"] == hint_uid:
        return _stored_device_id(row)
    return cas.new_device_id()


def _device_id_for_uid(uid: int | None) -> str:
    if uid is None:
        return cas.new_device_id()
    row = db.connect().execute("SELECT id, enc_device_id FROM users WHERE id=?", (uid,)).fetchone()
    return _stored_device_id(row)


def _stored_device_id(row) -> str:
    if row and row["enc_device_id"]:
        try:
            return crypto.decrypt(row["enc_device_id"], "device", row["id"]).decode()
        except crypto.CryptoError:
            # 换过 APP_SECRET_KEY 或数据损坏：当作新设备，用户重新走一次二次认证即可
            log.warning("user %s: stored device id cannot be decrypted", row["id"])
    return cas.new_device_id()


def _complete_login(p: PendingLogin) -> int:
    """CAS 验证通过即视为本人，首次登录自动建号。"""
    student_id, s = p.flow.username, p.flow.session
    try:
        data = jwxt.fetch_timetable(s, page_html=getattr(p.flow, "course_html", None))
    except jwxt.SessionExpired:
        raise LoginRejected("登录后访问教务系统失败，请稍后再试") from None
    except cas.CasError as e:
        raise LoginRejected(str(e)) from None
    except (ValueError, KeyError) as e:
        log.warning("login completed but timetable parse failed: %s", type(e).__name__)
        raise LoginRejected("登录成功，但教务系统返回的课表页面无法解析，请稍后再试") from None
    except Exception as e:  # 网络 / 学校服务异常：不把异常原文暴露给浏览器
        log.warning("login completed but timetable fetch failed: %s", type(e).__name__)
        raise LoginRejected("登录成功，但读取课表失败，请稍后再试") from None

    code = str(data["student"].get("code") or "")
    if not student_id:  # 扫码登录：以教务系统返回的学号为准
        if not code:
            raise LoginRejected("扫码登录成功，但未能从教务系统读取学号")
        student_id = code
    elif code and code != student_id:
        raise LoginRejected("教务系统返回的学号与输入不一致")
    p.flow.username = student_id

    # 与 delete_user 共用这把锁：账号已删、流程已摘掉时不再建号
    with _pending_lock:
        if p.flow_id not in _pending:
            raise LoginRejected("登录流程已取消")
        with db.tx() as c:
            row = c.execute("SELECT id FROM users WHERE student_id=?", (student_id,)).fetchone()
            if row:
                uid = row["id"]
            else:
                uid = c.execute("INSERT INTO users(student_id, created_at) VALUES(?,?)",
                                (student_id, db.now())).lastrowid
            c.execute("UPDATE users SET enc_device_id=? WHERE id=?",
                      (crypto.encrypt(p.flow.device_id.encode(), "device", uid), uid))
            if p.keep_creds:
                pass  # 扫码登录不涉及密码：保留用户原有的凭据设置
            elif p.password_to_store and PASSWORD_STORAGE_ENABLED:
                c.execute("UPDATE users SET cred_mode='password', enc_password=?, password_consent_at=? WHERE id=?",
                          (crypto.encrypt_password(p.password_to_store.encode(), uid, student_id), db.now(), uid))
            else:
                c.execute("UPDATE users SET cred_mode='cookie', enc_password=NULL, password_consent_at=NULL WHERE id=?",
                          (uid,))
            _save_success(c, uid, dump_cookies(s), data, "login")
    return uid


# ---------- 安卓 App 托管（双层兜底） ----------

APP_COOKIE_HOSTS = frozenset({cas.AUTH_HOST, "jwxt.nufe.edu.cn"})
APP_COOKIE_MAX = 40


def app_cookies(raw) -> list[dict]:
    """App 交来的学校 cookie：只收学校两个域名下的、格式干净的；其余一律丢弃。"""
    out = []
    for c in raw[:APP_COOKIE_MAX] if isinstance(raw, list) else []:
        if not isinstance(c, dict):
            continue
        name, value = c.get("name"), c.get("value")
        domain, path = str(c.get("domain") or "").lstrip("."), c.get("path") or "/"
        if (not isinstance(name, str) or not isinstance(value, str) or not isinstance(path, str)
                or domain not in APP_COOKIE_HOSTS or not name or len(name) > 128 or len(value) > 4096
                or _BAD_COOKIE_CHARS.search(name + value) or not path.startswith("/")):
            continue
        if name == "CASTGC" and domain != cas.AUTH_HOST:
            continue
        expires = c.get("expires")
        out.append({"name": name, "value": value, "domain": domain, "path": path[:256], "secure": True,
                    "expires": int(expires) if isinstance(expires, int | float) and expires > 0 else None})
    return out


def enroll_from_app(cookies: list[dict], keep_session: bool = True) -> int:
    """App 用自己在 WebView 里登录得到的学校会话来本站托管：本站拿它读一次课表，读得到学号就算本人。

    和网站登录同等强度：能拿出有效学校会话的人就是这个学号的主人。已有账号时只换登录态、不动已存的密码。
    keep_session=False（只把手机当 iOS 小组件 / 日历的数据源）：验明身份后不留学校会话，课表只靠手机推送；
    账号原本就有网站存的登录态 / 密码时保持不变，服务器照常刷新，两边谁新用谁。
    返回 uid；读不到课表抛 LoginRejected。
    """
    s = PoliteSession()
    load_cookies(s, cookies)
    try:
        try:
            data = jwxt.fetch_timetable(s)
        except jwxt.SessionExpired:
            if not cas.renew_with_tgc(s):
                raise LoginRejected("学校登录状态已失效，请在 App 内重新登录后再托管") from None
            data = jwxt.fetch_timetable(s)
    except LoginRejected:
        raise
    except cas.CasError as e:
        raise LoginRejected(str(e)) from None
    except (ValueError, KeyError) as e:
        log.warning("app enroll: timetable parse failed: %s", type(e).__name__)
        raise LoginRejected("教务系统返回的课表无法解析，请稍后再试") from None
    except UpstreamCooldown as e:
        raise LoginRejected(f"学校服务器繁忙，请约 {e.retry_after} 秒后再试") from None
    except Exception as e:
        log.warning("app enroll: timetable fetch failed: %s", type(e).__name__)
        raise LoginRejected("暂时无法通过学校验证，请稍后再试") from None
    code = str(data["student"].get("code") or "")
    if not code:
        raise LoginRejected("未能从教务系统读取学号")
    with db.tx() as c:
        row = c.execute("SELECT id, enc_device_id, cred_mode FROM users WHERE student_id=?", (code,)).fetchone()
        uid = row["id"] if row else c.execute(
            "INSERT INTO users(student_id, created_at) VALUES(?,?)", (code, db.now())).lastrowid
        if not row or not row["enc_device_id"]:
            c.execute("UPDATE users SET enc_device_id=? WHERE id=?",
                      (crypto.encrypt(cas.new_device_id().encode(), "device", uid), uid))
    lock = _locks[uid]
    if not lock.acquire(timeout=30):
        raise Busy()
    try:
        existing = _stored_cookies(uid)
        with db.tx() as c:
            _save_success(c, uid, dump_cookies(s) if keep_session else existing, data, "login")
            if not keep_session and not existing:
                # 本站手里没有任何学校凭据：标成“设备数据源”，调度器不再替他刷（刷也刷不了）
                c.execute("""UPDATE users SET enc_cookies=NULL, cred_mode='device', enc_password=NULL
                             WHERE id=? AND cred_mode<>'password'""", (uid,))
    finally:
        lock.release()
    return uid


def _stored_cookies(uid: int) -> list[dict]:
    row = db.connect().execute("SELECT enc_cookies FROM users WHERE id=?", (uid,)).fetchone()
    if not row or not row["enc_cookies"]:
        return []
    try:
        cookies = crypto.decrypt_json(row["enc_cookies"], "cookies", uid)
    except crypto.CryptoError:
        return []
    return cookies if isinstance(cookies, list) else []


class DevicePushRejected(Exception):
    """手机推来的课表不能用：给 App 看的原因。"""


DEVICE_PUSH_MAX_ACTIVITIES = 600
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def accept_device_push(uid: int, body: dict, now: float | None = None) -> dict:
    """绑定过的手机把本机刷到的课表推上来，本站据此给 iOS 小组件和日历订阅供数（“设备数据源”）。

    设备签名已经证明是本人的手机；内容仍按教务原始结构过一遍 jwxt.normalize，带了学号就必须是本人。
    本站自己刷到的那份更新时不覆盖（两边谁新用谁）。
    """
    now = db.now() if now is None else now
    sid = body.get("semester_id")
    name = body.get("semester_name")
    acts = body.get("activities")
    student = body.get("student") if isinstance(body.get("student"), dict) else {}
    updated = body.get("updated_at")
    if (not isinstance(sid, int) or isinstance(sid, bool) or not 1 <= sid <= 2_000_000
            or not isinstance(name, str) or not 0 < len(name) <= 64
            or not isinstance(acts, list) or len(acts) > DEVICE_PUSH_MAX_ACTIVITIES
            or not isinstance(updated, int | float) or updated <= 0):
        raise DevicePushRejected("课表格式不正确")
    updated = min(float(updated), now)
    user = db.connect().execute("SELECT student_id, last_success_at, semesters_json, current_semester_id FROM users WHERE id=?",
                                (uid,)).fetchone()
    if not user:
        raise UserGone(uid)
    # 设备签名已证明是这个账号的手机；本机数据可能读不到学号（部分教务返回不带），读得到就必须是本人
    code = str(student.get("code") or "")
    if code and code != user["student_id"]:
        raise DevicePushRejected("课表不是本人的")
    if user["current_semester_id"] == sid and (user["last_success_at"] or 0) >= updated:
        return {"ok": True, "stored": False, "message": "本站已有更新的课表"}
    normalized = jwxt.normalize({"studentTableVms": [{"name": student.get("name"), "code": user["student_id"],
                                                      "activities": [a for a in acts if isinstance(a, dict)]}]})
    semesters = [x for x in _json_list(user["semesters_json"]) if isinstance(x, dict)]
    if not any(x.get("id") == sid for x in semesters):
        semesters.append({"id": sid, "name": name})
    data = {"semester_id": sid, "semester_name": name, "current_semester_id": sid, "semesters": semesters,
            "semester_detail": None, **normalized}
    week1 = body.get("week1_monday")
    lock = _locks[uid]
    if not lock.acquire(timeout=10):
        return {"ok": False, "stored": False, "message": "服务器正在刷新，稍后再推"}
    try:
        cookies = _stored_cookies(uid)
        with db.tx() as c:
            changes = _save_success(c, uid, cookies, data, "device")
            if not cookies:
                c.execute("UPDATE users SET enc_cookies=NULL WHERE id=?", (uid,))
            if isinstance(week1, str) and _DATE_RE.match(week1):
                c.execute("UPDATE semesters SET week1_monday=COALESCE(week1_monday, ?) WHERE semester_id=?",
                          (week1, sid))
            # 手机的抓取时刻才是数据的真实新鲜度
            c.execute("UPDATE users SET last_success_at=? WHERE id=?", (updated, uid))
    finally:
        lock.release()
    _after_change(uid, "device", changes)
    return {"ok": True, "stored": True}


def forget_session_from_app(uid: int) -> None:
    """App 里关掉“服务器托管”但仍当数据源：删掉本站存的学校登录态，设备保留。网站上存的密码不动。"""
    with db.tx() as c:
        c.execute("UPDATE users SET enc_cookies=NULL, cred_mode='device' WHERE id=? AND cred_mode<>'password'", (uid,))
        c.execute("UPDATE users SET enc_cookies=NULL WHERE id=? AND cred_mode='password'", (uid,))


def leave_from_app(uid: int, device_id: str) -> None:
    """App 里取消托管：撤掉这台设备，删掉本站存的学校登录态（网站上另存的密码不动，需在网站设置里删）。"""
    with db.tx() as c:
        c.execute("DELETE FROM mobile_devices WHERE id=? AND user_id=?", (device_id, uid))
        c.execute("""UPDATE users SET enc_cookies=NULL, status='needs_rebind',
                     last_error='已在安卓 App 中取消托管，本站不再自动刷新' WHERE id=? AND cred_mode<>'password'""", (uid,))
        c.execute("UPDATE users SET enc_cookies=NULL WHERE id=? AND cred_mode='password'", (uid,))


def app_timetable(uid: int) -> dict:
    """给托管的 App：本站这份课表的原始结构（和 App 本机解析器同一套字段）与刷新状态。"""
    row = db.connect().execute("SELECT status, last_error, last_success_at FROM users WHERE id=?", (uid,)).fetchone()
    view = timetable_view(uid, None) or {}
    return {
        "status": row["status"] if row else "deleted",
        "last_error": row["last_error"] if row else None,
        "last_success_at": row["last_success_at"] if row else None,
        "semester_id": view.get("semester_id"),
        "semester_name": view.get("semester_name"),
        "week1_monday": view.get("week1_monday"),
        "fetched_at": view.get("fetched_at"),
        "student": {k: (view.get("student") or {}).get(k) for k in ("name", "code")},
        "activities": view.get("activities") or [],
    }


def forget_password(uid: int) -> None:
    with db.tx() as c:
        # 先覆盖再置空，降低 SQLite 页里留下旧密文的可能
        c.execute("UPDATE users SET enc_password=? WHERE id=?", (secrets.token_bytes(64), uid))
        c.execute("UPDATE users SET cred_mode='cookie', enc_password=NULL, password_consent_at=NULL WHERE id=?", (uid,))


def delete_user(uid: int) -> None:
    row = db.connect().execute(
        "SELECT student_id, enc_device_id FROM users WHERE id=?", (uid,)).fetchone()
    student_id = row["student_id"] if row else None
    device_id = None
    if row and row["enc_device_id"]:
        try:
            device_id = crypto.decrypt(row["enc_device_id"], "device", uid).decode()
        except crypto.CryptoError:
            device_id = None
    with _pending_lock:
        with db.tx() as c:
            c.execute("DELETE FROM users WHERE id=?", (uid,))  # 级联删除会话/课表/日志
            if student_id:
                c.execute("DELETE FROM users WHERE student_id=?", (student_id,))
        _ics_cache.pop(uid, None)
        for p in list(_pending.values()):
            same_user = bool(student_id) and p.flow.username == student_id
            same_device = bool(device_id) and p.flow.device_id == device_id
            if same_user or same_device:
                p.flow.wipe_password()
                p.password_to_store = None
                _pending.pop(p.flow_id, None)
        # 已完成、但浏览器还没来取结果的扫码登录：不能让它给已删除的账号建会话
        for fid, (_exp, result) in list(_completed.items()):
            if result.get("uid") == uid:
                _completed.pop(fid, None)


# ---------- 刷新 ----------

def refresh_user(uid: int, trigger: str, semester_id: int | None = None,
                 session: PoliteSession | None = None) -> dict:
    """同一用户同一时刻只允许一个刷新在跑。

    手动刷新另有两道闸，避免连点把全站的上游额度都占掉：
    - 该学期课表刚抓过（MANUAL_REFRESH_FRESH_SECONDS 内）且状态正常：直接返回已存的，不碰学校；
    - 否则距上次尝试（无论成败）不足 MANUAL_REFRESH_MIN_GAP 秒：抛 TooSoon。
    """
    lock = _locks[uid]
    if not lock.acquire(blocking=False):
        raise Busy()
    try:
        if trigger == "manual":
            cached = _manual_refresh_gate(uid, semester_id)
            if cached:
                return cached
        return _refresh_locked(uid, trigger, semester_id, session)
    finally:
        lock.release()


def _manual_refresh_gate(uid: int, semester_id: int | None) -> dict | None:
    conn = db.connect()
    user = conn.execute(
        "SELECT status, last_refresh_at, current_semester_id FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        raise KeyError(uid)
    now = db.now()
    # 先看新鲜度：刚登录 / 刚刷新完再点，给“已是最新”比报“太频繁”友好
    sid = semester_id or user["current_semester_id"]
    if user["status"] == "ok" and sid is not None:
        row = conn.execute("SELECT fetched_at FROM timetables WHERE user_id=? AND semester_id=?",
                           (uid, sid)).fetchone()
        if row and now - row["fetched_at"] < MANUAL_REFRESH_FRESH_SECONDS:
            return {"ok": True, "cached": True, "message": "课表刚刚同步过，已是最新"}
    since = now - (user["last_refresh_at"] or 0)
    if since < MANUAL_REFRESH_MIN_GAP:
        raise TooSoon(math.ceil(MANUAL_REFRESH_MIN_GAP - since))
    return None


def _renew_with_tgc(s: PoliteSession, uid: int) -> bool:
    try:
        renewed = cas.renew_with_tgc(s)
    except cas.CasError as e:
        # 认证中心或教务一时出错：别把用户打成“需要重新登录”，按普通失败退避
        log.warning("user %s: SSO renewal failed: %s", uid, type(e).__name__)
        raise UpstreamDown() from None
    if renewed:
        log.info("user %s: jwxt session renewed via CASTGC", uid)
        with db.tx() as c:
            c.execute("UPDATE users SET sso_touched_at=? WHERE id=?", (db.now(), uid))
    return renewed


def _refresh_locked(uid: int, trigger: str, semester_id: int | None,
                    session: PoliteSession | None = None) -> dict:
    """session：调用方可以传入换了传输层的会话（App 协助刷新的中继，见 assist.py）。"""
    user = db.connect().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        raise KeyError(uid)

    if user["cred_mode"] == "device" and not user["enc_cookies"]:
        # 课表由用户自己的安卓手机推送（设备数据源），本站没有学校凭据：不刷、也不记失败
        return {"ok": False, "status": "device", "message": "课表由你的安卓手机提供，请在 App 内刷新"}
    s = session or PoliteSession()
    if user["enc_cookies"]:
        try:
            cookies = crypto.decrypt_json(user["enc_cookies"], "cookies", uid)
            load_cookies(s, cookies)
        except (crypto.CryptoError, TypeError, KeyError) as e:
            log.warning("user %s: stored cookies unusable (%s)", uid, type(e).__name__)
            return _save_failure(uid, trigger, "needs_rebind",
                                 "保存的登录状态已无法读取（可能更换过服务器密钥），请重新登录本站")
    try:
        try:
            data = jwxt.fetch_timetable(s, semester_id)
        except jwxt.SessionExpired:
            # 先用 CASTGC 静默续期（扫码 / 不存密码的用户也能续上），失败才动用保存的密码
            if not _renew_with_tgc(s, uid):
                if not PASSWORD_STORAGE_ENABLED or user["cred_mode"] != "password" or not user["enc_password"]:
                    raise NeedsRebind("登录态已过期，请重新登录本站以续期") from None
                log.info("user %s: session expired, re-login with stored credential", uid)
                try:
                    device = (crypto.decrypt(user["enc_device_id"], "device", uid).decode()
                              if user["enc_device_id"] else None)
                    password = crypto.decrypt_password(user["enc_password"], uid, user["student_id"]).decode()
                except crypto.CryptoError:
                    log.warning("user %s: stored credential unusable", uid)
                    return _save_failure(uid, trigger, "needs_rebind",
                                         "保存的密码已无法解密（可能更换过服务器密钥），请重新登录本站")
                cas.login(s, user["student_id"], password, device)
                with db.tx() as c:  # 新的 CASTGC：保活重新从认证中心开始
                    c.execute("UPDATE users SET sso_touched_at=?, sso_lost_at=NULL WHERE id=?", (db.now(), uid))
            data = jwxt.fetch_timetable(s, semester_id)
    except cas.CaptchaRequired:
        return _save_failure(uid, trigger, "captcha",
                             "统一身份认证要求图形验证码，已暂停自动刷新。请退出后在本站重新登录（会让你填写验证码）")
    except cas.MfaRequired:
        return _save_failure(uid, trigger, "needs_rebind",
                             "统一身份认证要求手机短信二次认证，已暂停自动刷新。请退出后在本站重新登录完成短信验证")
    except cas.CasError as e:
        log.warning("user %s credential rejected: %s", uid, type(e).__name__)
        return _save_failure(uid, trigger, "needs_rebind", "保存的凭据无法登录，请重新登录本站")
    except NeedsRebind as e:
        return _save_failure(uid, trigger, "needs_rebind", str(e))
    except (UpstreamYield, RelayFailed):
        raise  # 让路 / 中继没走通：都不是这个用户的失败，不记账
    except UpstreamCooldown as e:
        # 全站在给学校“降温”，不是这个用户的问题：不记失败、不动退避
        return {"ok": False, "status": "cooldown", "retry_after": e.retry_after,
                "message": f"学校服务器繁忙，本站暂停访问中，请约 {e.retry_after} 秒后再试"}
    except Exception as e:  # 网络 / 学校服务异常：退避重试。不把异常原文存进库或返回给浏览器
        log.warning("user %s refresh failed: %s", uid, type(e).__name__)
        return _save_failure(uid, trigger, "error", "刷新失败，请稍后再试")

    try:
        with db.tx() as c:
            changes = _save_success(c, uid, dump_cookies(s), data, trigger)
    except UserGone:
        return {"ok": False, "status": "deleted", "message": "账号已删除，请重新登录"}
    _after_change(uid, trigger, changes)
    return {"ok": True}


def _after_change(uid: int, trigger: str, changes: dict | None) -> None:
    if not changes:
        return
    if trigger in ("scheduled", "widget", "assist", "device"):  # 后台发现的变动才发邮件；手动刷新 / 登录时用户就在屏幕前
        _notify_safely(notify.timetable_changed, uid, changes["semester_name"], changes["added"], changes["removed"])
    try:
        n = alert_classmates(uid, changes["semester_id"], changes["old"], changes["new"])
        if n:
            log.info("user %s: timetable changed, asked %s classmates to refresh", uid, n)
    except Exception as e:
        log.warning("alert classmates failed for user %s: %s", uid, type(e).__name__)


def _notify_safely(fn, *args) -> None:
    """通知失败（解密、入队等）只记日志，绝不影响刷新本身。"""
    try:
        fn(*args)
    except Exception as e:
        log.warning("notify %s failed for user %s: %s", fn.__name__, args[0], type(e).__name__)


# ---------- 动态刷新：信号、下一节课、按需请求 ----------

_SIGNAL_COLS = ("last_seen_at, last_success_at, last_change_at, next_class_at, refresh_requested_at, "
                "(enc_email IS NOT NULL AND notify_changes=1) AS subscribed")
NEXT_CLASS_LOOKAHEAD_DAYS = 60
NEXT_CLASS_RECHECK = 6 * 3600   # 两个月内都没课时，隔这么久再按已存课表重算一次（校历可能改了）
TOUCH_EVERY = 600               # 秒：last_seen_at 最多每 10 分钟写一次库
CHANGE_KEEP = 5                 # 最多留几次调课记录给小组件标记
_WIDGET_COLS = "widget_fetch, widget_fetch_ok_at, widget_fetch_fails, widget_fetch_pending_until"


def signals_of(row, peer_checked_at: float | None = None) -> refresh_policy.Signals:
    return refresh_policy.Signals(
        last_seen_at=row["last_seen_at"], last_success_at=row["last_success_at"],
        last_change_at=row["last_change_at"], next_class_at=row["next_class_at"],
        refresh_requested_at=row["refresh_requested_at"], subscribed=bool(row["subscribed"]),
        peer_checked_at=peer_checked_at)


def compute_next_class(uid: int, now: float | None = None, conn=None) -> tuple[float | None, list[str]]:
    """按已存的当前学期课表算下一节课：(开始时刻, 这一时刻开始的各节课的 slot_key)。不访问学校。"""
    conn = conn or db.connect()
    now = db.now() if now is None else now
    user = conn.execute("SELECT current_semester_id FROM users WHERE id=?", (uid,)).fetchone()
    view = timetable_view(uid, user["current_semester_id"] if user else None)
    if not view or not view.get("week1_monday"):
        return None, []
    try:
        week1 = dt.date.fromisoformat(view["week1_monday"])
    except ValueError:
        return None, []
    tz = ZoneInfo(TIMEZONE)
    today = dt.datetime.fromtimestamp(now, tz).date()
    acts = [a for a in (view.get("activities") or []) if isinstance(a, dict)]
    units = ics.unit_times(acts)
    sid = view["semester_id"]
    by_weekday: dict[int, list[tuple[dt.time, str, set[int]]]] = defaultdict(list)
    for a in acts:
        wd = a.get("weekday")
        if not isinstance(wd, int) or not 1 <= wd <= 7:
            continue
        weeks = {w for w in (a.get("weekIndexes") or []) if isinstance(w, int)}
        if not weeks:
            continue
        lesson = widget._lesson(a, units, {})
        if not lesson:
            continue
        h, m = map(int, lesson["start"].split(":"))
        by_weekday[wd].append((dt.time(h, m), slot_key(sid, a), weeks))
    if not by_weekday:
        return None, []
    for i in range(NEXT_CLASS_LOOKAHEAD_DAYS):
        day = today + dt.timedelta(days=i)
        day_items = by_weekday.get(day.isoweekday())
        if not day_items:
            continue
        week = widget.week_of(day, week1)
        starts = []
        for t_start, sk, weeks in day_items:
            if week in weeks:
                at = dt.datetime.combine(day, t_start, tz).timestamp()
                if at > now:
                    starts.append((at, sk))
        if starts:
            first = min(at for at, _k in starts)
            return first, sorted({k for at, k in starts if at == first})
    return None, []


def _store_next_class(c, uid: int, now: float) -> float | None:
    at, slots = compute_next_class(uid, now, c)
    c.execute("UPDATE users SET next_class_at=?, next_class_checked_at=?, next_class_slots=? WHERE id=?",
              (at, now, json.dumps(slots), uid))
    return at


def _peer_checked_at(conn, uid: int, semester_id, slots_json: str | None) -> float | None:
    """下一节课的每个 slot 都有同学（状态正常）刷到过：取其中最旧的那次；有一节没人上就是 None。

    同学刷新时若发现这节课变了，会直接把还停在旧安排的人排到队首（alert_classmates），
    所以“同学刚刷过、我没被叫醒”就等于这节课替我检查过了。
    """
    slots = [k for k in _json_list(slots_json) if isinstance(k, str)]
    if not slots or semester_id is None:
        return None
    marks = ",".join("?" * len(slots))
    rows = conn.execute(
        f"""SELECT s.slot_key, MAX(t.fetched_at) AS at FROM lesson_slots s
            JOIN users u ON u.id = s.user_id
            JOIN timetables t ON t.user_id = s.user_id AND t.semester_id = s.semester_id
            WHERE s.semester_id=? AND s.slot_key IN ({marks}) AND s.user_id<>? AND u.status='ok'
            GROUP BY s.slot_key""", (semester_id, *slots, uid)).fetchall()
    if len(rows) < len(slots):
        return None
    return min(r["at"] for r in rows)


def refresh_outlook(uid: int, now: float | None = None, row=None) -> dict:
    """给界面展示：目标间隔、原因、预计下次刷新。"""
    now = db.now() if now is None else now
    if row is None:
        row = db.connect().execute(f"SELECT status, next_refresh_at, {_SIGNAL_COLS} FROM users WHERE id=?",
                                   (uid,)).fetchone()
    if not row:
        return {}
    sig = signals_of(row)
    tgt = refresh_policy.target(sig, now)
    why = refresh_policy.reason(sig, now)
    if row["status"] in ("needs_rebind", "captcha"):
        nxt = None
    elif row["status"] == "error":
        nxt = row["next_refresh_at"]
    else:
        nxt = refresh_policy.next_due(sig, now)
    pending = bool(sig.refresh_requested_at and sig.refresh_requested_at > (sig.last_success_at or 0))
    return {"target_minutes": round(tgt.seconds / 60), "reason": why, "next_refresh_at": nxt,
            "pending": pending and row["status"] == "ok"}


_touched: dict[int, float] = globals().get("_touched") if "_touched" in globals() else {}
_touch_lock: threading.Lock = globals().get("_touch_lock") or threading.Lock()


def touch(uid: int, now: float | None = None) -> None:
    """记录“这个人还在用”（网站 / 小组件 / 日历订阅）。内存里节流，避免每次请求都写库。"""
    now = db.now() if now is None else now
    with _touch_lock:
        if now - _touched.get(uid, 0) < TOUCH_EVERY:
            return
        _touched[uid] = now
    with db.tx() as c:
        c.execute("UPDATE users SET last_seen_at=? WHERE id=?", (now, uid))


def request_refresh(uid: int, reason: str, now: float | None = None, conn=None) -> None:
    """请求尽快刷新：调度器会把这个人排到队首（仍受全站预算约束）。"""
    now = db.now() if now is None else now
    sql = ("""UPDATE users SET refresh_requested_at=?, refresh_request_reason=?
              WHERE id=? AND status='ok' AND (refresh_requested_at IS NULL
                    OR refresh_requested_at <= COALESCE(last_success_at, 0))""")
    if conn is not None:
        conn.execute(sql, (now, reason, uid))
    else:
        with db.tx() as c:
            c.execute(sql, (now, reason, uid))


def request_if_stale(uid: int, now: float | None = None, row=None) -> bool:
    """有人打开页面 / 小组件时：数据已经比他的目标间隔还旧，就排一次后台刷新。返回是否排了。"""
    now = db.now() if now is None else now
    if row is None:
        row = db.connect().execute(f"SELECT status, {_SIGNAL_COLS} FROM users WHERE id=?", (uid,)).fetchone()
    if not row or row["status"] != "ok":
        return False
    sig = signals_of(row)
    if sig.refresh_requested_at and sig.refresh_requested_at > (sig.last_success_at or 0):
        return True  # 已经在排队
    if refresh_policy.overdue(sig, now) < 1:
        return False
    request_refresh(uid, "打开时数据已过期", now)
    return True


def _widget_data_fresh(last_success_at: float | None, now: float) -> bool:
    """两分钟内刚同步过。读配置模块本身：测试会把 service 里的同名全局改成 0，不能跟着变。"""
    from .config import MANUAL_REFRESH_FRESH_SECONDS as window
    return bool(last_success_at and now - last_success_at < window)


def widget_refresh_state(uid: int, now: float | None = None) -> dict:
    """小组件 JSON 里的 refresh：是否在排队、是否刚刷过、策略上是否已经逾期。"""
    now = db.now() if now is None else now
    row = db.connect().execute(f"SELECT status, {_SIGNAL_COLS} FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        return {"pending": False, "due": False, "fresh": False}
    sig = signals_of(row)
    pending = bool(
        row["status"] == "ok" and sig.refresh_requested_at
        and (not sig.last_success_at or sig.refresh_requested_at > sig.last_success_at))
    fresh = _widget_data_fresh(sig.last_success_at, now)
    due = row["status"] == "ok" and not pending and not fresh and refresh_policy.overdue(sig, now) >= 1
    return {"pending": pending, "due": due, "fresh": fresh}


def widget_poll_after(uid: int, now: float | None = None) -> int | None:
    """小组件 JSON 里的 poll_after（秒）：开了代抓、状态正常的人才给，其余照脚本自己的节奏。"""
    now = db.now() if now is None else now
    row = db.connect().execute(f"SELECT status, widget_fetch, {_SIGNAL_COLS} FROM users WHERE id=?", (uid,)).fetchone()
    if not row or row["status"] != "ok" or not row["widget_fetch"]:
        return None
    return round(refresh_policy.widget_poll_after(signals_of(row), now))


def push_widget_refresh(uid: int, now: float | None = None) -> dict:
    """小组件明确要求刷新：状态正常、没在排队、也不是刚刚同步过，就插到队首。

    不看目标间隔。脚本只在快上课或用户点了“刷新课表”时才调用；两分钟内刚成功过则跳过，
    避免和网站上的手动刷新抢学校。真正访问学校仍走调度器的全站预算。
    """
    now = db.now() if now is None else now
    state = widget_refresh_state(uid, now)
    if state["pending"] or state["fresh"]:
        return state
    row = db.connect().execute("SELECT status FROM users WHERE id=?", (uid,)).fetchone()
    if row and row["status"] == "ok":
        request_refresh(uid, "小组件请求刷新", now)
    return widget_refresh_state(uid, now)


# ---------- 小组件代抓：用手机自己的网络向教务要课表 ----------
#
# 网页里的脚本读不到学校的响应（学校不发 CORS 头），登录也只能在服务器上完成；但 Scriptable 的请求是原生的，
# 不受这个限制。所以到期时把“去教务拿课表”这件事交给手机：服务器下发只含教务域 cookie 的任务
# （统一身份认证的 CASTGC 不出服务器），手机抓完把原始页面和 print-data 传回来，由服务器照常解析入库。
# 这样学校看到的是学生自己手机的 IP；手机做不了（会话过期、断网、没装小组件）时服务器照旧兜底。

WIDGET_TASK_TTL = 300            # 秒：任务有效期，也是这段时间里服务器不抢着刷
WIDGET_HEALTHY_FOR = 86400       # 这么久内代抓成功过、且之后没失败：到期后服务器先等手机
WIDGET_COURSE_HTML_MAX = 1024 * 1024


@dataclass
class WidgetTask:
    task_id: str
    uid: int
    semester_id: int
    expires_at: float


def _task_from_row(r) -> WidgetTask:
    return WidgetTask(r["task_id"], r["user_id"], r["semester_id"], r["expires_at"])


class WidgetUploadRejected(Exception):
    """上传的东西不能用（任务不对 / 过期 / 格式坏）：给脚本看的原因。"""


def has_active_widget_task(uid: int) -> bool:
    """该用户是否存在待交回的代抓任务：供 /upload 在读取 4MB 请求流前快速判权。"""
    return db.connect().execute("SELECT 1 FROM widget_tasks WHERE user_id=? LIMIT 1", (uid,)).fetchone() is not None


def widget_fetch_healthy(row, now: float) -> bool:
    return bool(row["widget_fetch"] and row["widget_fetch_ok_at"] and not row["widget_fetch_fails"]
                and now - row["widget_fetch_ok_at"] < WIDGET_HEALTHY_FOR)


def _jwxt_cookie_header(jar, url: str) -> str:
    """按 url 算出浏览器会带的 Cookie 头；统一身份认证（authserver）的 cookie 本来就不匹配教务域名，这里再保险一次。"""
    from requests.cookies import RequestsCookieJar, get_cookie_header
    only = RequestsCookieJar()
    for c in jar:
        if c.name != "CASTGC" and c.domain.lstrip(".") != cas.AUTH_HOST:
            only.set_cookie(c)
    return get_cookie_header(only, requests.Request("GET", url).prepare()) or ""


def widget_fetch_task(uid: int, now: float | None = None, pending: bool | None = None) -> dict | None:
    """小组件来取数据时：这个人此刻需要刷新（已在排队）、允许代抓、有可用的教务 cookie，就发一个任务。

    同一个人同时只有一个任务；还没过期的再来要就原样给（不顺延，免得坏掉的脚本一直挡着服务器）；
    上一个任务没交回来就过期了，记一次代抓失败。
    """
    now = db.now() if now is None else now
    row = db.connect().execute(
        f"SELECT status, enc_cookies, current_semester_id, semesters_json, {_WIDGET_COLS} FROM users WHERE id=?",
        (uid,)).fetchone()
    if pending is None:
        pending = widget_refresh_state(uid, now)["pending"]
    if (not row or row["status"] != "ok" or not row["widget_fetch"] or not row["enc_cookies"]
            or row["current_semester_id"] is None or not pending):
        return None
    with db.tx() as c:
        mine = [_task_from_row(r) for r in c.execute(
            "SELECT task_id, user_id, semester_id, expires_at FROM widget_tasks WHERE user_id=?", (uid,)).fetchall()]
        c.execute("DELETE FROM widget_tasks WHERE user_id=?", (uid,))
    live = next((t for t in mine if t.expires_at > now), None)
    lapsed = not live and bool(mine)
    if lapsed:
        _widget_fetch_failed(uid, "上次的代抓任务没有交回", fallback=False)
    try:
        cookies = crypto.decrypt_json(row["enc_cookies"], "cookies", uid)
        s = requests.Session()
        load_cookies(s, cookies)
    except (crypto.CryptoError, TypeError, KeyError):
        return None
    sid = row["current_semester_id"]
    urls = [("course", cas.COURSE_TABLE_URL), ("print", jwxt.print_data_url(sid))]
    if not jwxt.week1_known(sid, [x for x in _json_list(row["semesters_json"]) if isinstance(x, dict)]):
        urls.append(("detail", jwxt.semester_detail_url(sid)))
    steps = [{"kind": kind, "url": url, "cookie": _jwxt_cookie_header(s.cookies, url)} for kind, url in urls]
    if not steps[0]["cookie"]:
        return None  # 没有教务会话（只剩 CASTGC）：续期只能服务器做
    task = live or WidgetTask(secrets.token_urlsafe(18), uid, sid, now + WIDGET_TASK_TTL)
    with db.tx() as c:
        c.execute(
            "INSERT OR REPLACE INTO widget_tasks(task_id, user_id, semester_id, expires_at) VALUES(?,?,?,?)",
            (task.task_id, task.uid, task.semester_id, task.expires_at))
        if not live:
            c.execute("UPDATE users SET widget_fetch_pending_until=? WHERE id=?", (task.expires_at, uid))
    return {"id": task.task_id, "expires_in": max(0, round(task.expires_at - now)), "steps": steps,
            "headers": jwxt.PRINT_DATA_HEADERS, "login_hosts": [cas.AUTH_HOST]}


def _widget_fetch_failed(uid: int, why: str, fallback: bool = True) -> None:
    """代抓没成：记失败（之后到期不再等手机），需要的话立刻交给服务器刷。"""
    log.info("user %s: widget fetch failed: %s", uid, why)
    with db.tx() as c:
        c.execute("""UPDATE users SET widget_fetch_fails=widget_fetch_fails+1, widget_fetch_pending_until=NULL
                     WHERE id=?""", (uid,))
        if fallback:
            # 已经在排队的保持原样；没排的（比如任务签发后被别处刷过）就不必再刷
            c.execute("UPDATE users SET refresh_request_reason=? WHERE id=? AND refresh_requested_at IS NOT NULL",
                      (f"手机代抓未完成（{why}）", uid))


def _take_widget_task(uid: int, task_id, now: float) -> WidgetTask:
    if not isinstance(task_id, str):
        raise WidgetUploadRejected("任务不存在")
    with db.tx() as c:
        row = c.execute(
            "SELECT task_id, user_id, semester_id, expires_at FROM widget_tasks WHERE task_id=?",
            (task_id,)).fetchone()
        if not row or row["user_id"] != uid:
            raise WidgetUploadRejected("任务不存在")
        c.execute("DELETE FROM widget_tasks WHERE task_id=?", (task_id,))
        task = _task_from_row(row)
    if task.expires_at <= now:
        _widget_fetch_failed(uid, "任务已过期")
        raise WidgetUploadRejected("任务已过期")
    return task


def _logged_out(url) -> bool:
    if not isinstance(url, str):
        return False
    from urllib.parse import urlparse
    return urlparse(url).hostname == cas.AUTH_HOST or cas.is_jw_login_url(url)


_BAD_COOKIE_CHARS = re.compile(r"[\x00-\x1f\x7f;]")


def _merge_phone_cookies(cookies: list[dict], updates) -> list[dict]:
    """手机收到的新 cookie 值：只更新已有的、教务域名下的同名 cookie，不接受新增名字、别的域名或控制字符。"""
    if not isinstance(updates, list):
        return cookies
    fresh = {}
    for u in updates[:50]:
        if (isinstance(u, dict) and isinstance(u.get("name"), str) and isinstance(u.get("value"), str)
                and len(u["value"]) <= 4096 and not _BAD_COOKIE_CHARS.search(u["value"])):
            fresh[u["name"]] = u["value"]
    out = []
    for c in cookies:
        if (isinstance(c, dict) and c.get("name") in fresh and c.get("name") != "CASTGC"
                and str(c.get("domain", "")).lstrip(".") == "jwxt.nufe.edu.cn"):
            c = {**c, "value": fresh[c["name"]]}
        out.append(c)
    return out


def accept_widget_upload(uid: int, body: dict, now: float | None = None) -> dict:
    """手机交回代抓结果。成功就和服务器自己刷到的一样入库（trigger = widget）。"""
    now = db.now() if now is None else now
    task = _take_widget_task(uid, body.get("id"), now)
    error = body.get("error")
    if error or any(_logged_out(u) for u in (body.get("final_urls") or [])[:10]):
        why = "教务登录态已过期" if error in (None, "expired") else "手机网络或学校出错"
        _widget_fetch_failed(uid, why)
        return {"ok": False, "status": "fallback", "message": why}
    try:
        html, raw = body.get("course_html"), body.get("print_data")
        if not isinstance(html, str) or not isinstance(raw, str) or len(html) > WIDGET_COURSE_HTML_MAX:
            raise ValueError("上传内容不完整")
        page = jwxt.parse_course_page(html)
        print_data = jwxt.parse_print_data(raw)
        detail = None
        if isinstance(body.get("detail"), str) and len(body["detail"]) < 64 * 1024:
            with contextlib.suppress(ValueError):
                detail = json.loads(body["detail"])
    except ValueError as e:
        _widget_fetch_failed(uid, type(e).__name__)
        raise WidgetUploadRejected("上传的课表无法解析") from None
    if page.current_semester_id != task.semester_id:
        # 换学期了：课表页里的学期列表、校历都要重新拿，交给服务器
        _widget_fetch_failed(uid, "当前学期已变化")
        return {"ok": False, "status": "fallback", "message": "当前学期已变化"}
    data = jwxt.build_timetable(page, task.semester_id, print_data, detail)

    lock = _locks[uid]
    if not lock.acquire(blocking=False):
        return {"ok": False, "status": "busy", "message": "服务器正在刷新"}
    try:
        user = db.connect().execute("SELECT student_id, enc_cookies FROM users WHERE id=?", (uid,)).fetchone()
        if not user:
            raise UserGone(uid)
        # 手机传来的内容不可信：读不到学号也要拒绝，否则删掉 code 就能绕过本人校验。
        # 真遇到学校不返回学号的情况，这里记失败后由服务器照常刷新。
        code = str(data["student"].get("code") or "")
        if code != user["student_id"]:
            _widget_fetch_failed(uid, "学号不一致" if code else "未读到学号")
            raise WidgetUploadRejected("课表不是本人的")
        try:
            cookies = crypto.decrypt_json(user["enc_cookies"], "cookies", uid) if user["enc_cookies"] else []
        except crypto.CryptoError:
            cookies = []
        cookies = _merge_phone_cookies(cookies if isinstance(cookies, list) else [], body.get("set_cookies"))
        with db.tx() as c:
            changes = _save_success(c, uid, cookies, data, "widget")
            c.execute("""UPDATE users SET widget_fetch_ok_at=?, widget_fetch_fails=0, widget_fetch_pending_until=NULL
                         WHERE id=?""", (now, uid))
    finally:
        lock.release()
    _after_change(uid, "widget", changes)
    return {"ok": True}


def set_widget_fetch(uid: int, enabled: bool) -> None:
    with db.tx() as c:
        c.execute("""UPDATE users SET widget_fetch=?, widget_fetch_fails=0, widget_fetch_pending_until=NULL
                     WHERE id=?""", (1 if enabled else 0, uid))
        if not enabled:
            c.execute("DELETE FROM widget_tasks WHERE user_id=?", (uid,))


def candidates(now: float | None = None, limit: int = 50) -> list[tuple[float, int]]:
    """该刷的人，按逾期程度从高到低：[(score, uid)]。顺带惰性重算过期的“下一节课”。"""
    now = db.now() if now is None else now
    conn = db.connect()
    cols = (f"id, status, next_refresh_at, next_class_checked_at, next_class_slots, current_semester_id, "
            f"{_WIDGET_COLS}, {_SIGNAL_COLS}")
    rows = conn.execute(
        f"""SELECT {cols} FROM users
            WHERE cred_mode<>'device'
              AND (status='ok' OR (status='error' AND next_refresh_at IS NOT NULL AND next_refresh_at <= ?))""",
        (now,)).fetchall()
    stale_next = [r["id"] for r in rows
                  if (r["next_class_at"] and r["next_class_at"] <= now)
                  or (not r["next_class_at"] and now - (r["next_class_checked_at"] or 0) > NEXT_CLASS_RECHECK)]
    if stale_next:
        with db.tx() as c:
            for uid in stale_next:
                _store_next_class(c, uid, now)
        # 下一节课换了：重新读这些人的时刻和 slot
        rows = [r for r in rows if r["id"] not in set(stale_next)] + conn.execute(
            f"SELECT {cols} FROM users WHERE id IN ({','.join('?' * len(stale_next))})", stale_next).fetchall()
    out = []
    for r in rows:
        if r["status"] == "error":
            # 失败退避已到期的人按“该刷了”处理，但排在主动请求之后
            out.append((max(refresh_policy.overdue(signals_of(r), now), 1.0), r["id"]))
            continue
        score = _score(conn, r, now)
        if score >= 1:
            out.append((score, r["id"]))
    out.sort(reverse=True)
    return out[:limit]


def _score(conn, row, now: float) -> float:
    """逾期程度。只有卡在检查点上的人才去查同班同学（一条按 slot 的索引查询）。

    手机正在代抓、或这个人的小组件最近都能代抓时，到期后先给手机留一段宽限（见 refresh_policy），
    宽限内返回 0：服务器不碰学校。
    """
    sig, score = _overdue(conn, row, now)
    if score >= 1 and _waiting_for_widget(row, sig, now):
        return 0.0
    return score


def _overdue(conn, row, now: float) -> tuple[refresh_policy.Signals, float]:
    sig = signals_of(row)
    cp = refresh_policy.checkpoint(sig, now)
    if cp and now >= cp[0] and not refresh_policy.requested(sig):
        peer = _peer_checked_at(conn, row["id"], row["current_semester_id"], row["next_class_slots"])
        if peer:
            sig = signals_of(row, peer)
    return sig, refresh_policy.overdue(sig, now)


def waiting_for_phone(conn, row, now: float) -> bool:
    """已经到期，但服务器正让给手机代抓（管理员页展示用）。"""
    if row["status"] != "ok":
        return False
    sig, score = _overdue(conn, row, now)
    return score >= 1 and _waiting_for_widget(row, sig, now)


def _waiting_for_widget(row, sig: refresh_policy.Signals, now: float) -> bool:
    if not row["widget_fetch"]:
        return False
    if (row["widget_fetch_pending_until"] or 0) > now:
        return True  # 任务刚交给手机，它正在向学校要
    if not widget_fetch_healthy(row, now):
        return False
    grace = refresh_policy.widget_grace_until(sig, now)
    return grace is not None and now < grace


def still_due(uid: int, now: float | None = None) -> bool:
    """真正开刷前再看一眼：期间可能有人手动刷过了。"""
    now = db.now() if now is None else now
    conn = db.connect()
    row = conn.execute(
        f"""SELECT id, status, next_refresh_at, next_class_slots, current_semester_id, {_WIDGET_COLS}, {_SIGNAL_COLS}
            FROM users WHERE id=?""", (uid,)).fetchone()
    if not row:
        return False
    if row["status"] == "error":
        return bool(row["next_refresh_at"]) and row["next_refresh_at"] <= now
    # 同一个 tick 里同班同学可能刚替他检查过下一节课
    return row["status"] == "ok" and _score(conn, row, now) >= 1


# ---------- 同班同学：一人发现调课，其他人立刻排上 ----------

lesson_line = _lesson_line = widget.lesson_line

def slot_key(semester_id, a: dict) -> str:
    """同一节课的身份：课程、教师、星期、开始节次、周次（不含教室和具体钟点——它们正是会被调的部分）。"""
    teachers = ",".join(sorted(t for t in (a.get("teachers") or []) if isinstance(t, str)))
    weeks = a.get("weeksStr") or ",".join(str(w) for w in (a.get("weekIndexes") or []))
    raw = f"{semester_id}|{a.get('courseName')}|{teachers}|{a.get('weekday')}|{a.get('startUnit')}|{weeks}"
    return hashlib.sha1(raw.encode()).hexdigest()[:24]


def course_key(semester_id, a: dict) -> str:
    """同一门课：课程 + 教师。补课 / 加课是全新的排课条目，只能按课程找同学。"""
    teachers = ",".join(sorted(t for t in (a.get("teachers") or []) if isinstance(t, str)))
    return hashlib.sha1(f"{semester_id}|{a.get('courseName')}|{teachers}".encode()).hexdigest()[:24]


def _store_slots(c, uid: int, semester_id, activities: list) -> None:
    keys = {(slot_key(semester_id, a), course_key(semester_id, a)) for a in activities if isinstance(a, dict)}
    c.execute("DELETE FROM lesson_slots WHERE user_id=? AND semester_id=?", (uid, semester_id))
    c.executemany("INSERT OR IGNORE INTO lesson_slots(user_id, semester_id, slot_key, course_key) VALUES(?,?,?,?)",
                  [(uid, semester_id, k, ck) for k, ck in keys])


def alert_classmates(uid: int, semester_id, old: list, new: list) -> int:
    """课表变了：同班里还停在旧安排的人，请求他尽快刷新。返回人数。

    old / new：这次被改掉的排课（改动前）和新出现的排课（改动后）。
    - 改了时间 / 教室 / 周次：按改动前的 slot 找人，他存的课表里还有旧安排就提醒；
    - 补课 / 加课（这门课没有任何旧排课被改掉）：按课程找人，他存的课表里还没有这条新排课就提醒。
    已经刷到新安排的同学不会被反复叫醒，也不会互相连锁提醒。
    """
    old = [a for a in old if isinstance(a, dict)]
    new = [a for a in new if isinstance(a, dict)]
    stale_lines = {lesson_line(a) for a in old}
    slots = sorted({slot_key(semester_id, a) for a in old})
    touched = {course_key(semester_id, a) for a in old}
    extra: dict[str, set[str]] = {}
    for a in new:
        ck = course_key(semester_id, a)
        if ck not in touched:
            extra.setdefault(ck, set()).add(lesson_line(a))
    if not slots and not extra:
        return 0
    now = db.now()
    alerted = 0
    with db.tx() as c:
        rows = c.execute(
            f"""SELECT DISTINCT s.user_id, t.data_json FROM lesson_slots s
                JOIN timetables t ON t.user_id = s.user_id AND t.semester_id = s.semester_id
                WHERE s.semester_id=? AND s.user_id<>?
                  AND (s.slot_key IN ({",".join("?" * len(slots))}) OR s.course_key IN ({",".join("?" * len(extra))}))""",
            (semester_id, uid, *slots, *extra)).fetchall()
        for r in rows:
            payload = decrypt_timetable_data(r["data_json"], r["user_id"])
            acts = [a for a in (payload.get("activities") if payload else None) or [] if isinstance(a, dict)]
            lines = {lesson_line(a) for a in acts}
            mine = {course_key(semester_id, a) for a in acts}
            missing = any(ck in mine and not want <= lines for ck, want in extra.items())
            if lines & stale_lines or missing:
                request_refresh(r["user_id"], "同班同学的课表有变动", now, conn=c)
                alerted += 1
    return alerted


def timetable_diff(old: list, new: list) -> tuple[list[str], list[str]]:
    """(新增, 取消)；时间 / 地点 / 周次改了的课会同时出现在两边（调整后 / 调整前）。"""
    old_lines = {_lesson_line(a) for a in old if isinstance(a, dict)}
    new_lines = {_lesson_line(a) for a in new if isinstance(a, dict)}
    return sorted(new_lines - old_lines), sorted(old_lines - new_lines)


def encrypt_user_name(name: str | None, uid: int) -> bytes | None:
    if not name or not isinstance(name, str):
        return None
    return crypto.encrypt(name.encode(), "user_name", uid)


def decrypt_user_name(raw, uid: int) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    try:
        return crypto.decrypt(raw, "user_name", uid).decode()
    except (crypto.CryptoError, UnicodeDecodeError):
        return None


def encrypt_timetable_data(payload: dict, uid: int) -> bytes:
    return crypto.encrypt_json(payload, "timetable", uid)


def decrypt_timetable_data(raw, uid: int) -> dict | None:
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None
    if not isinstance(raw, bytes | bytearray | memoryview):
        return None
    try:
        payload = crypto.decrypt_json(bytes(raw), "timetable", uid)
    except crypto.CryptoError:
        return None
    return payload if isinstance(payload, dict) else None


def _old_activities(c, uid: int, semester_id) -> list | None:
    row = c.execute("SELECT data_json FROM timetables WHERE user_id=? AND semester_id=?",
                    (uid, semester_id)).fetchone()
    if not row:
        return None
    payload = decrypt_timetable_data(row["data_json"], uid)
    acts = payload.get("activities") if payload else None
    return acts if isinstance(acts, list) else None


def _save_success(c, uid: int, cookies: list[dict], data: dict, trigger: str) -> dict | None:
    """保存结果；返回课表变动 {"semester_name", "semester_id", "added", "removed", "old", "new"}
    （首次抓取或无变化时为 None）。added / removed 是给人看的行，old / new 是改动前后的排课本身。
    """
    now = db.now()
    if not c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
        raise UserGone(uid)
    old = _old_activities(c, uid, data["semester_id"])
    _save_semester(c, data)
    c.execute(
        """INSERT INTO timetables(user_id, semester_id, semester_name, data_json, fetched_at) VALUES(?,?,?,?,?)
           ON CONFLICT(user_id, semester_id) DO UPDATE SET semester_name=excluded.semester_name,
           data_json=excluded.data_json, fetched_at=excluded.fetched_at""",
        (uid, data["semester_id"], data["semester_name"],
         encrypt_timetable_data({"student": data["student"], "activities": data["activities"]}, uid), now))
    c.execute(
        """UPDATE users SET name=?, enc_cookies=?, status='ok', fail_count=0, last_error=NULL,
           last_refresh_at=?, last_success_at=?, current_semester_id=?, semesters_json=?,
           refresh_requested_at=NULL, refresh_request_reason=NULL, sso_session_only=?
           WHERE id=?""",
        (encrypt_user_name(data["student"].get("name"), uid), crypto.encrypt_json(cookies, "cookies", uid), now, now,
         data["current_semester_id"], json.dumps(data["semesters"], ensure_ascii=False),
         int(sso_session_only(cookies)), uid))
    if trigger == "login":
        # 刚登录：认证中心和教务的登录态都是新的，保活从现在算起
        c.execute("UPDATE users SET keepalive_at=?, sso_touched_at=?, sso_lost_at=NULL WHERE id=?", (now, now, uid))
    c.execute("INSERT INTO refresh_log(user_id, at, trigger, ok, message) VALUES(?,?,?,1,?)",
              (uid, now, trigger, f"{data['semester_name']}: {len(data['activities'])} 条排课"))
    _ics_cache.pop(uid, None)
    _store_slots(c, uid, data["semester_id"], data["activities"])
    changes = None
    if old is not None:
        added, removed = timetable_diff(old, data["activities"])
        if added or removed:
            gone, came = set(removed), set(added)
            changes = {"semester_name": data["semester_name"], "semester_id": data["semester_id"],
                       "added": added, "removed": removed,
                       "old": [a for a in old if isinstance(a, dict) and _lesson_line(a) in gone],
                       "new": [a for a in data["activities"] if isinstance(a, dict) and _lesson_line(a) in came]}
            _record_change(c, uid, now, changes)
    _store_next_class(c, uid, now)
    row = c.execute(f"SELECT {_SIGNAL_COLS} FROM users WHERE id=?", (uid,)).fetchone()
    c.execute("UPDATE users SET next_refresh_at=? WHERE id=?", (refresh_policy.next_due(signals_of(row), now), uid))
    return changes


def sso_session_only(cookies: list[dict]) -> bool:
    """认证中心的 CASTGC 是不是会话级的（没有过期时间）。

    账号密码登录带 rememberMe，CASTGC 能用半年；扫码登录的表单没有“记住我”，只发会话级 CASTGC，
    认证中心几个小时后就不认了，教务会话一过期就只能本人重新登录。
    """
    return not any(c.get("name") == "CASTGC" and isinstance(c.get("expires"), int | float) for c in cookies)


KEEPALIVE_EVERY = SSO_KEEPALIVE_MINUTES * 60
KEEPALIVE_IDLE = SSO_KEEPALIVE_IDLE_DAYS * 86400


def keepalive_candidates(now: float | None = None) -> list[int]:
    """该保活的人：登录态还在、离上次保活已满间隔。

    扫码登录（会话级 CASTGC）、没存密码的人排在前面：他们的登录态最不经放，预算紧时先保他们；
    账号密码登录的 CASTGC 能用半年，但教务会话闲置半小时左右就过期，保着就省得每次刷新都重新走一遍 SSO。
    刷新失败正在退避（error）的人也保活：学校一时出错不该让登录态跟着闲置过期。
    很久没用本站（网站 / 小组件 / 日历订阅）的人不保活。
    """
    now = db.now() if now is None else now
    rows = db.connect().execute(
        """SELECT id FROM users
           WHERE status IN ('ok', 'error') AND enc_cookies IS NOT NULL
             AND COALESCE(keepalive_at, 0) <= ? AND COALESCE(last_seen_at, last_success_at, 0) > ?
           ORDER BY (sso_session_only=1 AND cred_mode<>'password') DESC, COALESCE(keepalive_at, 0)""",
        (now - KEEPALIVE_EVERY, now - KEEPALIVE_IDLE)).fetchall()
    return [r["id"] for r in rows]


def keepalive_state(row, now: float | None = None) -> dict | None:
    """给前端看的保活状态：

    {"every_minutes", "last_at": 上次保活, "sso_ok": 认证中心是否还认, "sso_lost_at", "paused": 太久没用、已停止保活}
    """
    now = db.now() if now is None else now
    seen = row["last_seen_at"] or row["last_success_at"] or 0
    return {"every_minutes": SSO_KEEPALIVE_MINUTES, "last_at": row["keepalive_at"],
            "sso_ok": row["sso_lost_at"] is None, "sso_lost_at": row["sso_lost_at"],
            "paused": now - seen > KEEPALIVE_IDLE}


def keepalive_user(uid: int) -> dict:
    """碰一下认证中心和教务，让登录态别因为闲置过期。

    - 认证中心：签一张 ticket 不兑换（cas.touch_sso）。会话级 CASTGC 多半有闲置超时，常碰着能撑到它的绝对上限；
      发现已经不认了就记下 sso_lost_at，之后不再碰（存了密码的人下次刷新会用密码重新登录，再恢复）。
    - 教务：不跟随跳转地打开课表页（jwxt.touch）。教务会话常碰着就不会过期，认证中心不认了也还能接着刷课表；
      教务会话已过期就排一次刷新，交给正常流程（能续期就续期，续不上提示重新登录）。
    返回 {"jwxt": bool, "sso": bool | None}（sso 为 None：之前已发现失效，这次没碰）。
    """
    lock = _locks[uid]
    if not lock.acquire(blocking=False):
        raise Busy()
    try:
        row = db.connect().execute(
            "SELECT enc_cookies, sso_touched_at, sso_lost_at FROM users WHERE id=?", (uid,)).fetchone()
        if not row or not row["enc_cookies"]:
            raise KeyError(uid)
        s = PoliteSession()
        load_cookies(s, crypto.decrypt_json(row["enc_cookies"], "cookies", uid))
        now = db.now()
        try:
            jw_ok = jwxt.touch(s)
            sso_ok = None if row["sso_lost_at"] else cas.touch_sso(s)
        except UpstreamYield:
            raise
        except Exception as e:  # 学校一时出错：这一轮算碰过，下个间隔再来，不改状态
            log.warning("user %s: keepalive failed: %s", uid, type(e).__name__)
            with db.tx() as c:
                c.execute("UPDATE users SET keepalive_at=? WHERE id=?", (now, uid))
            return {"jwxt": None, "sso": None}
        with db.tx() as c:
            c.execute("UPDATE users SET keepalive_at=? WHERE id=?", (now, uid))
            if jw_ok or sso_ok:  # 学校可能顺手换了 cookie：还有效就存回去
                c.execute("UPDATE users SET enc_cookies=? WHERE id=?",
                          (crypto.encrypt_json(dump_cookies(s), "cookies", uid), uid))
            if sso_ok:
                c.execute("UPDATE users SET sso_touched_at=? WHERE id=?", (now, uid))
            elif sso_ok is False:
                c.execute("UPDATE users SET sso_lost_at=? WHERE id=?", (now, uid))
            if not jw_ok:
                request_refresh(uid, "教务登录态已过期", now, conn=c)  # 只对状态正常的人生效；退避中的按原计划重试
        if sso_ok is False:
            # 记下会话级 CASTGC 实际能撑多久，用来调保活间隔
            idle = (now - row["sso_touched_at"]) / 3600 if row["sso_touched_at"] else None
            log.info("user %s: session-only CASTGC no longer accepted (%s since last accepted)", uid,
                     f"{idle:.1f}h" if idle is not None else "unknown")
        if not jw_ok:
            log.info("user %s: keepalive found jwxt session expired, refresh requested", uid)
        return {"jwxt": jw_ok, "sso": sso_ok}
    finally:
        lock.release()


def sso_short(cred_mode: str | None, session_only) -> bool:
    """自动刷新撑不了多久、需要提醒用户的情况。存了密码的人过期后会自动重新登录，不用提醒。"""
    return bool(session_only) and cred_mode != "password"


def _decrypt_changes(raw, uid: int) -> list:
    if isinstance(raw, bytes | bytearray | memoryview):
        try:
            value = crypto.decrypt_json(bytes(raw), "changes", uid)
        except crypto.CryptoError:
            return []
        return value if isinstance(value, list) else []
    return _json_list(raw)


def _record_change(c, uid: int, now: float, changes: dict) -> None:
    """记下这次调课（改动前后的排课），小组件在课表上标出“调”和“停”。只留最近几次。"""
    row = c.execute("SELECT recent_changes_json FROM users WHERE id=?", (uid,)).fetchone()
    events = [ev for ev in _decrypt_changes(row["recent_changes_json"] if row else None, uid)
              if isinstance(ev, dict) and isinstance(ev.get("at"), int | float) and now - ev["at"] < widget.CHANGE_SHOW]
    events.append({"at": now, "semester_id": changes["semester_id"],
                   "removed": changes["old"][:40], "added": changes["new"][:40]})
    c.execute("UPDATE users SET last_change_at=?, recent_changes_json=? WHERE id=?",
              (now, crypto.encrypt_json(events[-CHANGE_KEEP:], "changes", uid), uid))


def recent_changes(uid: int) -> list:
    row = db.connect().execute("SELECT recent_changes_json FROM users WHERE id=?", (uid,)).fetchone()
    return _decrypt_changes(row["recent_changes_json"] if row else None, uid)


def _save_failure(uid: int, trigger: str, status: str, message: str) -> dict:
    now = db.now()
    with db.tx() as c:
        row = c.execute("SELECT fail_count, status FROM users WHERE id=?", (uid,)).fetchone()
        if row is None:
            return {"ok": False, "status": "deleted", "message": "账号已删除，请重新登录"}
        prev_status = row["status"]
        fails = (row["fail_count"] or 0) + 1
        if status == "error":
            # 指数退避：30min, 1h, 2h ... 上限 24h；连续失败达到阈值后在界面提示用户
            nxt = now + min(1800 * 2 ** (fails - 1), 86400) * random.uniform(0.9, 1.2)
            if fails >= REFRESH_MAX_FAILS:
                message = f"已连续失败 {fails} 次。{message}"
        else:
            nxt = None  # 验证码 / 需重新绑定：停止自动重试
        # 失败后由退避控制重试，清掉待刷新请求，避免反复抢队首
        c.execute("""UPDATE users SET status=?, fail_count=?, last_error=?, last_refresh_at=?, next_refresh_at=?,
                     refresh_requested_at=NULL, refresh_request_reason=NULL WHERE id=?""",
                  (status, fails, message, now, nxt, uid))
        c.execute("INSERT INTO refresh_log(user_id, at, trigger, ok, message) VALUES(?,?,?,0,?)",
                  (uid, now, trigger, message))
    # 只提醒后台刷新里的“状态变化”：暂停那一刻发一次；连续失败恰好达到阈值时发一次
    if trigger in ("scheduled", "assist") and ((status != "error" and prev_status != status)
                                   or (status == "error" and fails == REFRESH_MAX_FAILS)):
        _notify_safely(notify.refresh_paused, uid, status, message)
    return {"ok": False, "status": status, "message": message}


# ---------- 学期 / 校历 ----------

def _detail_start(detail) -> str | None:
    if not isinstance(detail, dict):
        return None
    for k in ("startDate", "beginDate", "startOn", "start"):
        v = detail.get(k)
        if isinstance(v, str) and len(v) >= 10:
            try:
                d = dt.date.fromisoformat(v[:10])
            except ValueError:
                continue
            return (d - dt.timedelta(days=d.weekday())).isoformat()  # 对齐到周一
    return None


def _save_semester(c, data: dict) -> None:
    week1 = _detail_start(data.get("semester_detail"))
    c.execute(
        """INSERT INTO semesters(semester_id, name, week1_monday) VALUES(?,?,?)
           ON CONFLICT(semester_id) DO UPDATE SET name=excluded.name,
           week1_monday=COALESCE(excluded.week1_monday, semesters.week1_monday)""",
        (data["semester_id"], data["semester_name"], week1))


def week1_monday(semester_id: int, name: str | None) -> str | None:
    ov = semester_overrides()
    if name and name in ov:
        return ov[name]
    if str(semester_id) in ov:
        return ov[str(semester_id)]
    row = db.connect().execute(
        "SELECT week1_monday FROM semesters WHERE semester_id=?", (semester_id,)).fetchone()
    return row["week1_monday"] if row else None


def _json_list(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def timetable_view(uid: int, semester_id: int | None) -> dict | None:
    conn = db.connect()
    user = conn.execute("SELECT current_semester_id FROM users WHERE id=?", (uid,)).fetchone()
    sid = semester_id or (user and user["current_semester_id"])
    if sid is None:
        return None
    row = conn.execute(
        "SELECT semester_id, semester_name, data_json, fetched_at FROM timetables WHERE user_id=? AND semester_id=?",
        (uid, sid)).fetchone()
    if not row:
        return None
    payload = decrypt_timetable_data(row["data_json"], uid)
    if not payload:
        log.warning("user %s semester %s: stored timetable is corrupt", uid, sid)
        return None
    return {
        "semester_id": row["semester_id"],
        "semester_name": row["semester_name"],
        "week1_monday": week1_monday(row["semester_id"], row["semester_name"]),
        "fetched_at": row["fetched_at"],
        **payload,
    }


def account_snapshot(uid: int) -> dict | None:
    """刷新接口带回的账号状态，前端不必再请求 /api/me。"""
    row = db.connect().execute(
        """SELECT student_id, name, cred_mode, status, last_error, fail_count, last_refresh_at,
                  last_success_at, next_refresh_at, current_semester_id, semesters_json, sso_session_only,
                  keepalive_at, sso_lost_at, last_seen_at
           FROM users WHERE id=?""", (uid,)).fetchone()
    if not row:
        return None
    return {
        "student_id": row["student_id"],
        "name": decrypt_user_name(row["name"], uid),
        "cred_mode": row["cred_mode"],
        "status": row["status"],
        "last_error": row["last_error"],
        "fail_count": row["fail_count"],
        "last_refresh_at": row["last_refresh_at"],
        "last_success_at": row["last_success_at"],
        "next_refresh_at": row["next_refresh_at"],
        "current_semester_id": row["current_semester_id"],
        "semesters": _json_list(row["semesters_json"]),
        "sso_short": sso_short(row["cred_mode"], row["sso_session_only"]),
        "keepalive": keepalive_state(row),
        **_outlook_fields(uid),
    }


def _outlook_fields(uid: int) -> dict:
    o = refresh_outlook(uid)
    return {"next_refresh_at": o.get("next_refresh_at"), "refresh_target_minutes": o.get("target_minutes"),
            "refresh_reason": o.get("reason")}




def housekeeping() -> None:
    """清过期会话 / 日志 / 登录流程，并回收已删除用户的刷新锁。"""
    _purge_pending()
    now = db.now()
    with db.tx() as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        c.execute("DELETE FROM admin_sessions WHERE expires_at < ?", (now,))
        c.execute(
            """DELETE FROM refresh_log WHERE id IN (
                   SELECT id FROM (
                       SELECT id, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY at DESC, id DESC) AS rn
                       FROM refresh_log
                   ) WHERE rn > ?
               )""", (LOG_KEEP,))
        live = {r["id"] for r in c.execute("SELECT id FROM users").fetchall()}
    for uid in list(_locks):
        if uid not in live:
            _locks.pop(uid, None)


def ics_body(uid: int, student_id: str, alarm_minutes: int = 0) -> tuple[str, str]:
    """一次查出全部学期。按影响正文的全部输入缓存，返回 (正文哈希/ETag, 正文)。"""
    rows = db.connect().execute(
        """SELECT t.semester_id, t.semester_name, t.data_json, t.fetched_at, s.week1_monday AS db_week1
           FROM timetables t LEFT JOIN semesters s ON s.semester_id = t.semester_id
           WHERE t.user_id=?""", (uid,)).fetchall()
    # 校历可能被 semesters.json 或别的用户刷新改掉；把影响正文的输入都放进缓存键
    ov = semester_overrides()
    key = (
        max((r["fetched_at"] for r in rows), default=0.0),
        len(rows),
        tuple((r["semester_id"], r["semester_name"], r["db_week1"]) for r in rows),
        tuple(sorted(ov.items())),
        alarm_minutes,
    )
    cached = _ics_cache.get(uid)
    if cached and cached[0] == key:
        return cached[1], cached[2]
    sems = []
    for r in rows:
        name = r["semester_name"]
        if name and name in ov:
            week1 = ov[name]
        elif str(r["semester_id"]) in ov:
            week1 = ov[str(r["semester_id"])]
        else:
            week1 = r["db_week1"]
        if not week1:
            continue
        try:
            payload = decrypt_timetable_data(r["data_json"], uid)
            if not isinstance(payload, dict):
                raise ValueError("not an object")
            dt.date.fromisoformat(week1)  # 坏日期跳过，不影响其他学期
        except (TypeError, ValueError):
            log.warning("user %s semester %s: skip invalid stored timetable/week1", uid, r["semester_id"])
            continue
        view = {
            "semester_id": r["semester_id"],
            "semester_name": name,
            **payload,
        }
        sems.append((view, week1))
    body = ics.build(student_id, sems, alarm_minutes)
    # ETag 必须跟随正文（校历/学期数据变化时正文会变），不能只看 fetched_at
    version = hashlib.sha256(body.encode()).hexdigest()[:24]
    _ics_cache[uid] = (key, version, body)
    return version, body
