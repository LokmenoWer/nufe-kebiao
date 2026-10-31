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

from . import cas, crypto, db, ics, jwxt
from .config import (
    MAX_USER_REFRESH_TIMES,
    REFRESH_ALLOWED_HOURS,
    REFRESH_INTERVAL_HOURS,
    REFRESH_MAX_FAILS,
    TIMEZONE,
    semester_overrides,
)
from .polite import PoliteSession, UpstreamYield, dump_cookies, load_cookies

log = logging.getLogger("kebiao.service")

_locks: defaultdict[int, threading.Lock] = defaultdict(threading.Lock)


class NeedsRebind(Exception):
    pass


class Busy(Exception):
    pass


class UserGone(Exception):
    """刷新过程中账号被删除（或数据已不存在）。"""


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
        return p.flow.captcha_image()


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
            raise LoginRejected("当前不需要短信验证")
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
    if p.flow.stage == "qr":
        out["qr_status"] = p.flow.qr_status
    return out


def _advance(p: PendingLogin, step, stash: bool = False) -> dict:
    try:
        stage = step()
    except cas.CasError as e:
        if p.flow.stage in ("captcha", "mfa", "qr"):
            return _need(p, str(e))  # 验证码错了之类：留在当前步骤让用户重试
        _drop(p)
        raise LoginRejected(f"统一身份认证登录失败：{e}") from None
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
    """手动刷新不限次数：对学校的总频率由 polite 的全站限速（每秒最多 1 次）保证。
    同一用户同一时刻只允许一个刷新在跑。"""
    lock = _locks[uid]
    if not lock.acquire(blocking=False):
        raise Busy()
    try:
        return _refresh_locked(uid, trigger, semester_id)
    finally:
        lock.release()


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
    except Exception as e:  # 网络 / 学校服务异常：退避重试。不把异常原文存进库或返回给浏览器
        log.warning("user %s refresh failed: %s", uid, type(e).__name__)
        return _save_failure(uid, trigger, "error", "刷新失败，请稍后再试")

    try:
        with db.tx() as c:
            _save_success(c, uid, s, data, trigger)
    except UserGone:
        return {"ok": False, "status": "deleted", "message": "账号已删除，请重新登录"}
    return {"ok": True}


def next_scheduled(hours: list[int] | None, now: float | None = None) -> float:
    """下次自动刷新时间。

    用户选了整点：取最近一个（至少 1 小时之后的）整点，再在该小时内随机偏移 0~45 分钟，
    避免所有人同一时刻打到学校。没选：按默认间隔 ± 15% 抖动。
    """
    now = db.now() if now is None else now
    if not hours:
        return now + REFRESH_INTERVAL_HOURS * 3600 * random.uniform(0.85, 1.15)
    tz = ZoneInfo(TIMEZONE)
    base = dt.datetime.fromtimestamp(now, tz).replace(minute=0, second=0, microsecond=0)
    starts = sorted(
        (base.replace(hour=h) + dt.timedelta(days=d)).timestamp()
        for d in (0, 1, 2) for h in hours
    )
    start = next(s for s in starts if s >= now + 3600)
    return start + random.uniform(0, 45 * 60)


def validate_refresh_hours(hours: list[int]) -> list[int]:
    hours = sorted(set(hours))
    if len(hours) > MAX_USER_REFRESH_TIMES:
        raise ValueError(f"每天最多选择 {MAX_USER_REFRESH_TIMES} 个刷新时间")
    bad = [h for h in hours if h not in REFRESH_ALLOWED_HOURS]
    if bad:
        raise ValueError(f"{', '.join(f'{h}:00' for h in bad)} 不在允许的时段内（为减轻学校服务器压力，仅开放夜间和午间）")
    return hours


def set_refresh_hours(uid: int, hours: list[int]) -> float | None:
    """保存用户自选刷新时间，并据此重排下次刷新（暂停/退避中的状态不动）。"""
    hours = validate_refresh_hours(hours)
    with db.tx() as c:
        c.execute("UPDATE users SET refresh_hours=? WHERE id=?", (json.dumps(hours) if hours else None, uid))
        row = c.execute("SELECT status, next_refresh_at FROM users WHERE id=?", (uid,)).fetchone()
        if row is None:
            raise KeyError(uid)
        if row["status"] != "ok" or row["next_refresh_at"] is None:
            return row["next_refresh_at"]
        nxt = next_scheduled(hours)
        c.execute("UPDATE users SET next_refresh_at=? WHERE id=?", (nxt, uid))
        return nxt


def _user_hours(c, uid: int) -> list[int] | None:
    row = c.execute("SELECT refresh_hours FROM users WHERE id=?", (uid,)).fetchone()
    if not row or not row["refresh_hours"]:
        return None
    try:
        hours = json.loads(row["refresh_hours"])
    except (TypeError, ValueError):
        return None
    return [h for h in hours if isinstance(h, int)] if isinstance(hours, list) else None


def _save_success(c, uid: int, s, data: dict, trigger: str) -> None:
    now = db.now()
    if not c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
        raise UserGone(uid)
    _save_semester(c, data)
    c.execute(
        """INSERT INTO timetables(user_id, semester_id, semester_name, data_json, fetched_at) VALUES(?,?,?,?,?)
           ON CONFLICT(user_id, semester_id) DO UPDATE SET semester_name=excluded.semester_name,
           data_json=excluded.data_json, fetched_at=excluded.fetched_at""",
        (uid, data["semester_id"], data["semester_name"],
         json.dumps({"student": data["student"], "activities": data["activities"]}, ensure_ascii=False), now))
    c.execute(
        """UPDATE users SET name=?, enc_cookies=?, status='ok', fail_count=0, last_error=NULL,
           last_refresh_at=?, last_success_at=?, next_refresh_at=?, current_semester_id=?, semesters_json=?
           WHERE id=?""",
        (data["student"].get("name"), crypto.encrypt_json(dump_cookies(s), "cookies", uid), now, now,
         next_scheduled(_user_hours(c, uid)), data["current_semester_id"],
         json.dumps(data["semesters"], ensure_ascii=False), uid))
    c.execute("INSERT INTO refresh_log(user_id, at, trigger, ok, message) VALUES(?,?,?,1,?)",
              (uid, now, trigger, f"{data['semester_name']}: {len(data['activities'])} 条排课"))
    _ics_cache.pop(uid, None)


def _save_failure(uid: int, trigger: str, status: str, message: str) -> dict:
    now = db.now()
    with db.tx() as c:
        row = c.execute("SELECT fail_count FROM users WHERE id=?", (uid,)).fetchone()
        if row is None:
            return {"ok": False, "status": "deleted", "message": "账号已删除，请重新登录"}
        fails = (row["fail_count"] or 0) + 1
        if status == "error":
            # 指数退避：30min, 1h, 2h ... 上限 24h；连续失败达到阈值后在界面提示用户
            nxt = now + min(1800 * 2 ** (fails - 1), 86400) * random.uniform(0.9, 1.2)
            if fails >= REFRESH_MAX_FAILS:
                message = f"已连续失败 {fails} 次。{message}"
        else:
            nxt = None  # 验证码 / 需重新绑定：停止自动重试
        c.execute("UPDATE users SET status=?, fail_count=?, last_error=?, last_refresh_at=?, next_refresh_at=? WHERE id=?",
                  (status, fails, message, now, nxt, uid))
        c.execute("INSERT INTO refresh_log(user_id, at, trigger, ok, message) VALUES(?,?,?,0,?)",
                  (uid, now, trigger, message))
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
                  last_success_at, next_refresh_at, current_semester_id, semesters_json, refresh_hours
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
        "refresh_hours": _json_list(row["refresh_hours"]),
    }


def due_users(limit: int) -> list[int]:
    rows = db.connect().execute(
        """SELECT id FROM users WHERE status IN ('ok','error') AND next_refresh_at IS NOT NULL
           AND next_refresh_at <= ? ORDER BY next_refresh_at LIMIT ?""", (db.now(), limit)).fetchall()
    return [r["id"] for r in rows]


def still_due(uid: int) -> bool:
    row = db.connect().execute(
        """SELECT 1 FROM users WHERE id=? AND status IN ('ok','error') AND next_refresh_at IS NOT NULL
           AND next_refresh_at <= ?""", (uid, db.now())).fetchone()
    return row is not None


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
