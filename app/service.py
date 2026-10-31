"""业务核心：代登录、抓取、凭据保管、刷新状态机。

凭据策略：
- 默认 cookie 模式：只保存（加密的）CAS TGC + 教务会话 cookie，不保存密码。
  TGC 过期后无法后台续期，标记 needs_rebind，请用户重新登录一次。
- password 模式（用户明确勾选同意后）：额外保存加密的教务密码，登录态过期时自动重登。
  即便如此，刷新也总是先尝试 cookie，只有 cookie 失效才解密密码。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import math
import random
import secrets
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from . import cas, crypto, db, ics, jwxt, notify, refresh_policy, widget
from .config import (
    MANUAL_REFRESH_FRESH_SECONDS,
    MANUAL_REFRESH_MIN_GAP,
    REFRESH_MAX_FAILS,
    TIMEZONE,
    semester_overrides,
)
from .polite import PoliteSession, UpstreamCooldown, UpstreamYield, dump_cookies, load_cookies

log = logging.getLogger("kebiao.service")

_locks: defaultdict[int, threading.Lock] = defaultdict(threading.Lock)


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


_pending: dict[str, PendingLogin] = {}
_pending_lock = threading.RLock()
# 后台轮询完成登录后，下一次 HTTP poll 从这里取结果（flow_id → (到期时间, 返回值)）
_completed: dict[str, tuple[float, dict]] = {}
_ics_cache: dict[int, tuple[tuple, str, str]] = {}


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
        password_to_store=password if remember_password else None,
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
            elif p.password_to_store:
                c.execute("UPDATE users SET cred_mode='password', enc_password=?, password_consent_at=? WHERE id=?",
                          (crypto.encrypt(p.password_to_store.encode(), "password", uid), db.now(), uid))
            else:
                c.execute("UPDATE users SET cred_mode='cookie', enc_password=NULL, password_consent_at=NULL WHERE id=?",
                          (uid,))
            _save_success(c, uid, s, data, "login")
    return uid


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

def refresh_user(uid: int, trigger: str, semester_id: int | None = None) -> dict:
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
        return _refresh_locked(uid, trigger, semester_id)
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
    return renewed


def _refresh_locked(uid: int, trigger: str, semester_id: int | None) -> dict:
    user = db.connect().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        raise KeyError(uid)

    s = PoliteSession()
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
                if user["cred_mode"] != "password" or not user["enc_password"]:
                    raise NeedsRebind("登录态已过期，请重新登录本站以续期") from None
                log.info("user %s: session expired, re-login with stored credential", uid)
                try:
                    device = (crypto.decrypt(user["enc_device_id"], "device", uid).decode()
                              if user["enc_device_id"] else None)
                    password = crypto.decrypt(user["enc_password"], "password", uid).decode()
                except crypto.CryptoError:
                    log.warning("user %s: stored credential unusable", uid)
                    return _save_failure(uid, trigger, "needs_rebind",
                                         "保存的密码已无法解密（可能更换过服务器密钥），请重新登录本站")
                cas.login(s, user["student_id"], password, device)
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
    except UpstreamYield:
        raise
    except UpstreamCooldown as e:
        # 全站在给学校“降温”，不是这个用户的问题：不记失败、不动退避
        return {"ok": False, "status": "cooldown", "retry_after": e.retry_after,
                "message": f"学校服务器繁忙，本站暂停访问中，请约 {e.retry_after} 秒后再试"}
    except Exception as e:  # 网络 / 学校服务异常：退避重试。不把异常原文存进库或返回给浏览器
        log.warning("user %s refresh failed: %s", uid, type(e).__name__)
        return _save_failure(uid, trigger, "error", "刷新失败，请稍后再试")

    try:
        with db.tx() as c:
            changes = _save_success(c, uid, s, data, trigger)
    except UserGone:
        return {"ok": False, "status": "deleted", "message": "账号已删除，请重新登录"}
    if changes:
        if trigger == "scheduled":
            _notify_safely(notify.timetable_changed, uid, changes["semester_name"], changes["added"], changes["removed"])
        try:
            n = alert_classmates(uid, changes["semester_id"], changes["slots"], changes["removed"])
            if n:
                log.info("user %s: timetable changed, asked %s classmates to refresh", uid, n)
        except Exception as e:
            log.warning("alert classmates failed for user %s: %s", uid, type(e).__name__)
    return {"ok": True}


def _notify_safely(fn, *args) -> None:
    """通知失败（解密、入队等）只记日志，绝不影响刷新本身。"""
    try:
        fn(*args)
    except Exception as e:
        log.warning("notify %s failed for user %s: %s", fn.__name__, args[0], type(e).__name__)


# ---------- 动态刷新：信号、下一节课、按需请求 ----------

_SIGNAL_COLS = "last_seen_at, last_success_at, last_change_at, next_class_at, refresh_requested_at"
NEXT_CLASS_LOOKAHEAD_DAYS = 60
NEXT_CLASS_RECHECK = 6 * 3600   # 两个月内都没课时，隔这么久再按已存课表重算一次（校历可能改了）
TOUCH_EVERY = 600               # 秒：last_seen_at 最多每 10 分钟写一次库


def signals_of(row) -> refresh_policy.Signals:
    return refresh_policy.Signals(
        last_seen_at=row["last_seen_at"], last_success_at=row["last_success_at"],
        last_change_at=row["last_change_at"], next_class_at=row["next_class_at"],
        refresh_requested_at=row["refresh_requested_at"])


def compute_next_class(uid: int, now: float | None = None, conn=None) -> float | None:
    """按已存的当前学期课表算下一节课的开始时刻（不访问学校）。"""
    conn = conn or db.connect()
    now = db.now() if now is None else now
    user = conn.execute("SELECT current_semester_id FROM users WHERE id=?", (uid,)).fetchone()
    view = timetable_view(uid, user["current_semester_id"] if user else None)
    if not view or not view.get("week1_monday"):
        return None
    try:
        week1 = dt.date.fromisoformat(view["week1_monday"])
    except ValueError:
        return None
    tz = ZoneInfo(TIMEZONE)
    today = dt.datetime.fromtimestamp(now, tz).date()
    acts = [a for a in (view.get("activities") or []) if isinstance(a, dict)]
    units = ics.unit_times(acts)
    for i in range(NEXT_CLASS_LOOKAHEAD_DAYS):
        day = today + dt.timedelta(days=i)
        for lesson in widget.lessons_on(day, acts, week1, units, {}):
            h, m = map(int, lesson["start"].split(":"))
            at = dt.datetime.combine(day, dt.time(h, m), tz).timestamp()
            if at > now:
                return at
    return None


def _store_next_class(c, uid: int, now: float) -> float | None:
    at = compute_next_class(uid, now, c)
    c.execute("UPDATE users SET next_class_at=?, next_class_checked_at=? WHERE id=?", (at, now, uid))
    return at


def refresh_outlook(uid: int, now: float | None = None) -> dict:
    """给界面展示：目标间隔、原因、预计下次刷新。"""
    now = db.now() if now is None else now
    row = db.connect().execute(f"SELECT status, next_refresh_at, {_SIGNAL_COLS} FROM users WHERE id=?",
                               (uid,)).fetchone()
    if not row:
        return {}
    sig = signals_of(row)
    tgt = refresh_policy.target(sig, now)
    if row["status"] in ("needs_rebind", "captcha"):
        nxt = None
    elif row["status"] == "error":
        nxt = row["next_refresh_at"]
    else:
        nxt = refresh_policy.next_due(sig, now)
    pending = bool(sig.refresh_requested_at and sig.refresh_requested_at > (sig.last_success_at or 0))
    return {"target_minutes": round(tgt.seconds / 60), "reason": tgt.reason, "next_refresh_at": nxt,
            "pending": pending and row["status"] == "ok"}


_touched: dict[int, float] = {}
_touch_lock = threading.Lock()


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


def request_if_stale(uid: int, now: float | None = None) -> bool:
    """有人打开页面 / 小组件时：数据已经比他的目标间隔还旧，就排一次后台刷新。返回是否排了。"""
    now = db.now() if now is None else now
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


def candidates(now: float | None = None, limit: int = 50) -> list[tuple[float, int]]:
    """该刷的人，按逾期程度从高到低：[(score, uid)]。顺带惰性重算过期的“下一节课”。"""
    now = db.now() if now is None else now
    conn = db.connect()
    rows = conn.execute(
        f"""SELECT id, status, next_refresh_at, next_class_checked_at, {_SIGNAL_COLS} FROM users
            WHERE status='ok' OR (status='error' AND next_refresh_at IS NOT NULL AND next_refresh_at <= ?)""",
        (now,)).fetchall()
    stale_next = [r["id"] for r in rows
                  if (r["next_class_at"] and r["next_class_at"] <= now)
                  or (not r["next_class_at"] and now - (r["next_class_checked_at"] or 0) > NEXT_CLASS_RECHECK)]
    if stale_next:
        with db.tx() as c:
            fresh = {uid: _store_next_class(c, uid, now) for uid in stale_next}
    else:
        fresh = {}
    out = []
    for r in rows:
        sig = signals_of(r)
        if r["id"] in fresh:
            sig = refresh_policy.Signals(sig.last_seen_at, sig.last_success_at, sig.last_change_at,
                                         fresh[r["id"]], sig.refresh_requested_at)
        # 失败退避已到期的人按“该刷了”处理，但排在主动请求之后
        score = max(refresh_policy.overdue(sig, now), 1.0) if r["status"] == "error" else refresh_policy.overdue(sig, now)
        if score >= 1:
            out.append((score, r["id"]))
    out.sort(reverse=True)
    return out[:limit]


def still_due(uid: int, now: float | None = None) -> bool:
    """真正开刷前再看一眼：期间可能有人手动刷过了。"""
    now = db.now() if now is None else now
    row = db.connect().execute(f"SELECT status, next_refresh_at, {_SIGNAL_COLS} FROM users WHERE id=?",
                               (uid,)).fetchone()
    if not row:
        return False
    if row["status"] == "error":
        return bool(row["next_refresh_at"]) and row["next_refresh_at"] <= now
    return row["status"] == "ok" and refresh_policy.overdue(signals_of(row), now) >= 1


# ---------- 同班同学：一人发现调课，其他人立刻排上 ----------

def slot_key(semester_id, a: dict) -> str:
    """同一节课的身份：课程、教师、星期、开始节次、周次（不含教室和具体钟点——它们正是会被调的部分）。"""
    teachers = ",".join(sorted(t for t in (a.get("teachers") or []) if isinstance(t, str)))
    weeks = a.get("weeksStr") or ",".join(str(w) for w in (a.get("weekIndexes") or []))
    raw = f"{semester_id}|{a.get('courseName')}|{teachers}|{a.get('weekday')}|{a.get('startUnit')}|{weeks}"
    return hashlib.sha1(raw.encode()).hexdigest()[:24]


def _store_slots(c, uid: int, semester_id, activities: list) -> None:
    keys = {slot_key(semester_id, a) for a in activities if isinstance(a, dict)}
    c.execute("DELETE FROM lesson_slots WHERE user_id=? AND semester_id=?", (uid, semester_id))
    c.executemany("INSERT OR IGNORE INTO lesson_slots(user_id, semester_id, slot_key) VALUES(?,?,?)",
                  [(uid, semester_id, k) for k in keys])


def alert_classmates(uid: int, semester_id, keys: list[str], old_lines: list[str]) -> int:
    """被改动的课（按改动前的身份）还有谁在上，且他存的课表里还是旧安排：请求他尽快刷新。返回人数。

    只提醒“还停留在旧安排”的人：已经刷到新安排的同学不会被反复叫醒，也不会互相连锁提醒。
    """
    if not keys or not old_lines:
        return 0
    now = db.now()
    stale_lines = set(old_lines)
    alerted = 0
    with db.tx() as c:
        marks = ",".join("?" * len(keys))
        rows = c.execute(
            f"""SELECT DISTINCT s.user_id, t.data_json FROM lesson_slots s
                JOIN timetables t ON t.user_id = s.user_id AND t.semester_id = s.semester_id
                WHERE s.semester_id=? AND s.slot_key IN ({marks}) AND s.user_id<>?""",
            (semester_id, *keys, uid)).fetchall()
        for r in rows:
            try:
                acts = json.loads(r["data_json"]).get("activities") or []
            except (TypeError, ValueError, AttributeError):
                acts = []
            if any(isinstance(a, dict) and _lesson_line(a) in stale_lines for a in acts):
                request_refresh(r["user_id"], "同班同学的课表有变动", now, conn=c)
                alerted += 1
    return alerted


_WEEKDAY = "一二三四五六日"


def _lesson_line(a: dict) -> str:
    """一条排课的人类可读描述，也作为比较课表变动的键。"""
    day = a.get("weekday")
    day_txt = f"周{_WEEKDAY[day - 1]}" if isinstance(day, int) and 1 <= day <= 7 else "周?"
    start, end = a.get("startUnit"), a.get("endUnit")
    units = f"第{start}–{end}节" if start and end and end != start else (f"第{start}节" if start else "")
    clock = f"{a.get('startTime') or ''}–{a.get('endTime') or ''}".strip("–")
    when = " ".join(x for x in (day_txt, units, clock) if x)
    weeks = a.get("weeksStr") or ",".join(str(w) for w in (a.get("weekIndexes") or []))
    room = a.get("room") or a.get("building") or "地点待定"
    return f"{a.get('courseName') or '未命名课程'} · {when} · {room} · 第{weeks}周"


def timetable_diff(old: list, new: list) -> tuple[list[str], list[str]]:
    """(新增, 取消)；时间 / 地点 / 周次改了的课会同时出现在两边（调整后 / 调整前）。"""
    old_lines = {_lesson_line(a) for a in old if isinstance(a, dict)}
    new_lines = {_lesson_line(a) for a in new if isinstance(a, dict)}
    return sorted(new_lines - old_lines), sorted(old_lines - new_lines)


def _old_activities(c, uid: int, semester_id) -> list | None:
    row = c.execute("SELECT data_json FROM timetables WHERE user_id=? AND semester_id=?",
                    (uid, semester_id)).fetchone()
    if not row:
        return None
    try:
        acts = json.loads(row["data_json"]).get("activities")
    except (TypeError, ValueError, AttributeError):
        return None
    return acts if isinstance(acts, list) else None


def _save_success(c, uid: int, s, data: dict, trigger: str) -> dict | None:
    """保存结果；返回课表变动 {"semester_name", "added", "removed", "slots"}（首次抓取或无变化时为 None）。

    slots：被改动的课在改动前的 slot_key，用来提醒同班同学。
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
         json.dumps({"student": data["student"], "activities": data["activities"]}, ensure_ascii=False), now))
    c.execute(
        """UPDATE users SET name=?, enc_cookies=?, status='ok', fail_count=0, last_error=NULL,
           last_refresh_at=?, last_success_at=?, current_semester_id=?, semesters_json=?,
           refresh_requested_at=NULL, refresh_request_reason=NULL
           WHERE id=?""",
        (data["student"].get("name"), crypto.encrypt_json(dump_cookies(s), "cookies", uid), now, now,
         data["current_semester_id"], json.dumps(data["semesters"], ensure_ascii=False), uid))
    c.execute("INSERT INTO refresh_log(user_id, at, trigger, ok, message) VALUES(?,?,?,1,?)",
              (uid, now, trigger, f"{data['semester_name']}: {len(data['activities'])} 条排课"))
    _ics_cache.pop(uid, None)
    _store_slots(c, uid, data["semester_id"], data["activities"])
    changes = None
    if old is not None:
        added, removed = timetable_diff(old, data["activities"])
        if added or removed:
            new_lines = {_lesson_line(a) for a in data["activities"] if isinstance(a, dict)}
            slots = sorted({slot_key(data["semester_id"], a) for a in old
                            if isinstance(a, dict) and _lesson_line(a) not in new_lines})
            changes = {"semester_name": data["semester_name"], "semester_id": data["semester_id"],
                       "added": added, "removed": removed, "slots": slots}
            c.execute("UPDATE users SET last_change_at=? WHERE id=?", (now, uid))
    _store_next_class(c, uid, now)
    row = c.execute(f"SELECT {_SIGNAL_COLS} FROM users WHERE id=?", (uid,)).fetchone()
    c.execute("UPDATE users SET next_refresh_at=? WHERE id=?", (refresh_policy.next_due(signals_of(row), now), uid))
    return changes


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
    if trigger == "scheduled" and ((status != "error" and prev_status != status)
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
    try:
        payload = json.loads(row["data_json"])
    except (TypeError, ValueError):
        log.warning("user %s semester %s: stored timetable is corrupt", uid, sid)
        return None
    if not isinstance(payload, dict):
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
                  last_success_at, next_refresh_at, current_semester_id, semesters_json
           FROM users WHERE id=?""", (uid,)).fetchone()
    if not row:
        return None
    return {
        "student_id": row["student_id"],
        "name": row["name"],
        "cred_mode": row["cred_mode"],
        "status": row["status"],
        "last_error": row["last_error"],
        "fail_count": row["fail_count"],
        "last_refresh_at": row["last_refresh_at"],
        "last_success_at": row["last_success_at"],
        "next_refresh_at": row["next_refresh_at"],
        "current_semester_id": row["current_semester_id"],
        "semesters": _json_list(row["semesters_json"]),
        **_outlook_fields(uid),
    }


def _outlook_fields(uid: int) -> dict:
    o = refresh_outlook(uid)
    return {"next_refresh_at": o.get("next_refresh_at"), "refresh_target_minutes": o.get("target_minutes"),
            "refresh_reason": o.get("reason")}




def housekeeping() -> None:
    """清过期会话 / 日志 / 登录流程，并回收已删除用户的刷新锁。"""
    _purge_pending()
    with db.tx() as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (db.now(),))
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


def ics_body(uid: int, student_id: str) -> tuple[str, str]:
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
            payload = json.loads(r["data_json"])
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
    body = ics.build(student_id, sems)
    # ETag 必须跟随正文（校历/学期数据变化时正文会变），不能只看 fetched_at
    version = hashlib.sha256(body.encode()).hexdigest()[:24]
    _ics_cache[uid] = (key, version, body)
    return version, body
