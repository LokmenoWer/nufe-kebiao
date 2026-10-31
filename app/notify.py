"""邮件通知：绑定邮箱（验证码确认）+ 两类提醒。

- 刷新暂停：后台刷新遇到需要重新登录 / 验证码，或连续失败达到阈值。用户没打开网页时也能知道。
- 课表变动：后台刷新发现同一学期的课程有增删改（时间、地点、周次）。

只在后台刷新（trigger=scheduled）时提醒：手动刷新、登录时用户就在屏幕前。
邮箱加密落库；验证码只存 HMAC，10 分钟有效，最多试 5 次。
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets

from . import crypto, db, mailer
from .config import APP_SECRET_KEY, MAIL_ENABLED, PUBLIC_BASE_URL
from .security import RateLimiter

log = logging.getLogger("kebiao.notify")

CODE_TTL = 600          # 秒
CODE_MAX_ATTEMPTS = 5
RESEND_GAP = 60         # 秒：同一用户两次发验证码的最小间隔
MAX_LISTED_CHANGES = 15

# 每封验证 / 测试邮件都真的从我们的邮箱发出去：按人、按 IP 限量，防止被拿来轰炸别人的邮箱
mails_by_user = RateLimiter(5, 3600)
mails_by_ip = RateLimiter(10, 3600)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}(\.[A-Za-z0-9\-]{1,63})*\.[A-Za-z]{2,24}")

_FOOTER = (
    "\n\n——\n南财课表（非官方第三方工具）自动发送，请勿直接回复。\n"
    f"不想再收到这类邮件，可在网站「设置 → 邮件通知」里关闭或解绑邮箱：{PUBLIC_BASE_URL}/\n"
)


class EmailError(Exception):
    def __init__(self, message: str, status: int = 400, retry_after: int | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def normalize_email(raw: str) -> str:
    email = (raw or "").strip()
    if len(email) > 254 or not _EMAIL_RE.fullmatch(email):
        raise EmailError("邮箱格式不正确")
    local, _, domain = email.partition("@")
    return f"{local}@{domain.lower()}"


def _code_hash(uid: int, code: str) -> str:
    return hmac.new(APP_SECRET_KEY, f"email-code:{uid}:{code}".encode(), hashlib.sha256).hexdigest()


def _require_mail() -> None:
    if not MAIL_ENABLED:
        raise EmailError("本站没有配置邮件发送，暂不能使用邮件通知", 503)


def _rate_limit(uid: int, ip: str) -> None:
    if not mails_by_user.hit(str(uid)) or not mails_by_ip.hit(ip):
        raise EmailError("发送邮件过于频繁，请一小时后再试", 429, 3600)


# ---------- 绑定 ----------

def start_verification(uid: int, raw_email: str, ip: str) -> dict:
    _require_mail()
    email = normalize_email(raw_email)
    now = db.now()
    row = db.connect().execute("SELECT sent_at FROM email_verifications WHERE user_id=?", (uid,)).fetchone()
    if row and now - row["sent_at"] < RESEND_GAP:
        wait = int(RESEND_GAP - (now - row["sent_at"])) + 1
        raise EmailError(f"请 {wait} 秒后再重新发送", 429, wait)
    _rate_limit(uid, ip)
    code = f"{secrets.randbelow(10**6):06d}"
    with db.tx() as c:
        if not c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
            raise KeyError(uid)
        c.execute(
            """INSERT INTO email_verifications(user_id, enc_email, code_hash, sent_at, expires_at, attempts)
               VALUES(?,?,?,?,?,0)
               ON CONFLICT(user_id) DO UPDATE SET enc_email=excluded.enc_email, code_hash=excluded.code_hash,
               sent_at=excluded.sent_at, expires_at=excluded.expires_at, attempts=0""",
            (uid, crypto.encrypt(email.encode(), "email-pending", uid), _code_hash(uid, code), now, now + CODE_TTL))
    mailer.send(email, f"【南财课表】邮箱验证码 {code}",
                f"你正在南财课表绑定通知邮箱，验证码是：\n\n    {code}\n\n"
                f"{CODE_TTL // 60} 分钟内有效。如果不是你本人操作，忽略这封邮件即可，邮箱不会被绑定。" + _FOOTER)
    return {"pending_email": email, "retry_after": RESEND_GAP, "expires_in": CODE_TTL}


def confirm(uid: int, code: str) -> dict:
    code = (code or "").strip()
    # 出错信息在事务提交之后再抛：否则 tx() 回滚，失败次数根本记不上
    error = None
    with db.tx() as c:
        row = c.execute("SELECT * FROM email_verifications WHERE user_id=?", (uid,)).fetchone()
        if not row:
            error = "请先获取验证码"
        elif row["expires_at"] < db.now() or row["attempts"] >= CODE_MAX_ATTEMPTS:
            c.execute("DELETE FROM email_verifications WHERE user_id=?", (uid,))
            error = "验证码已失效，请重新获取"
        elif not hmac.compare_digest(row["code_hash"], _code_hash(uid, code)):
            c.execute("UPDATE email_verifications SET attempts=attempts+1 WHERE user_id=?", (uid,))
            left = CODE_MAX_ATTEMPTS - row["attempts"] - 1
            error = f"验证码不正确，还可以再试 {left} 次" if left > 0 else "验证码不正确，请重新获取"
        else:
            c.execute("DELETE FROM email_verifications WHERE user_id=?", (uid,))
            try:
                email = crypto.decrypt(row["enc_email"], "email-pending", uid).decode()
            except crypto.CryptoError:
                error = "验证信息已失效，请重新获取验证码"
            else:
                c.execute("UPDATE users SET enc_email=?, email_verified_at=? WHERE id=?",
                          (crypto.encrypt(email.encode(), "email", uid), db.now(), uid))
    if error:
        raise EmailError(error)
    return snapshot(uid)


def remove(uid: int) -> dict:
    with db.tx() as c:
        c.execute("UPDATE users SET enc_email=NULL, email_verified_at=NULL WHERE id=?", (uid,))
        c.execute("DELETE FROM email_verifications WHERE user_id=?", (uid,))
    return snapshot(uid)


def set_prefs(uid: int, notify_status: bool, notify_changes: bool) -> dict:
    with db.tx() as c:
        c.execute("UPDATE users SET notify_status=?, notify_changes=? WHERE id=?",
                  (int(notify_status), int(notify_changes), uid))
    return snapshot(uid)


def send_test(uid: int, ip: str) -> dict:
    _require_mail()
    email = _email_of(uid)
    if not email:
        raise EmailError("还没有绑定邮箱")
    _rate_limit(uid, ip)
    mailer.send(email, "【南财课表】测试邮件",
                "这是一封测试邮件：能收到说明邮件通知已经可用。\n"
                "如果它进了垃圾箱，请把发件人加入通讯录或白名单，免得错过刷新暂停的提醒。" + _FOOTER)
    return {"ok": True}


def snapshot(uid: int) -> dict:
    """给 /api/me 和设置页：邮箱本人可见（明文），日志里才打码。"""
    out = {"available": MAIL_ENABLED, "email": None, "pending_email": None,
           "notify_status": True, "notify_changes": True}
    conn = db.connect()
    row = conn.execute("SELECT enc_email, notify_status, notify_changes FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        return out
    out["notify_status"] = bool(row["notify_status"])
    out["notify_changes"] = bool(row["notify_changes"])
    out["email"] = _decrypt_email(uid, row["enc_email"], "email")
    pending = conn.execute("SELECT enc_email, expires_at FROM email_verifications WHERE user_id=?", (uid,)).fetchone()
    if pending and pending["expires_at"] > db.now():
        out["pending_email"] = _decrypt_email(uid, pending["enc_email"], "email-pending")
    return out


def _decrypt_email(uid: int, blob, purpose: str) -> str | None:
    if not blob:
        return None
    try:
        return crypto.decrypt(blob, purpose, uid).decode()
    except crypto.CryptoError:
        log.warning("user %s: stored %s cannot be decrypted", uid, purpose)
        return None


def _email_of(uid: int, pref: str | None = None) -> str | None:
    row = db.connect().execute(
        "SELECT enc_email, notify_status, notify_changes FROM users WHERE id=?", (uid,)).fetchone()
    if not row or not row["enc_email"] or (pref and not row[pref]):
        return None
    return _decrypt_email(uid, row["enc_email"], "email")


# ---------- 提醒 ----------

def refresh_paused(uid: int, status: str, message: str) -> None:
    if not MAIL_ENABLED:
        return
    email = _email_of(uid, "notify_status")
    if not email:
        return
    if status == "error":
        subject = "【南财课表】课表自动刷新连续失败"
        lead = "后台自动刷新连续失败，会继续按退避间隔重试；如果学校系统正常但一直失败，可以打开网站手动刷新或重新登录。"
    else:
        subject = "【南财课表】需要你重新登录，自动刷新已暂停"
        lead = "后台自动刷新已暂停：学校统一身份认证需要你本人完成一次登录（或验证码 / 二次认证）。在那之前，课表和日历订阅都不会更新。"
    mailer.send(email, subject, f"{lead}\n\n原因：{message}\n\n打开网站重新登录：{PUBLIC_BASE_URL}/" + _FOOTER)


def timetable_changed(uid: int, semester_name: str, added: list[str], removed: list[str]) -> None:
    if not MAIL_ENABLED or not (added or removed):
        return
    email = _email_of(uid, "notify_changes")
    if not email:
        return
    parts = [f"后台刷新发现 {semester_name} 的课表有变动："]
    for title, items in (("新增 / 调整后", added), ("取消 / 调整前", removed)):
        if items:
            parts.append(f"\n{title}：")
            parts += [f"  · {line}" for line in items[:MAX_LISTED_CHANGES]]
            if len(items) > MAX_LISTED_CHANGES:
                parts.append(f"  …… 另有 {len(items) - MAX_LISTED_CHANGES} 条")
    parts.append(f"\n以教务系统为准。查看课表：{PUBLIC_BASE_URL}/")
    mailer.send(email, f"【南财课表】{semester_name} 课表有变动", "\n".join(parts) + _FOOTER)
