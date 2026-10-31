"""发邮件：单线程后台队列 + SMTP。

调用方只管 send()，入队即返回，SMTP 慢或暂时连不上都不会拖住 Web 请求和刷新线程。
发送失败重试两次（间隔递增），仍失败就丢弃并记日志——通知是锦上添花，不能反过来影响主流程。
日志里只记打码后的收件人，不记正文（正文可能有课表 / 验证码）。
"""
from __future__ import annotations

import contextlib
import logging
import queue
import smtplib
import ssl
import threading
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from .config import (
    MAIL_ENABLED,
    MAIL_ENCRYPTION,
    MAIL_FROM_ADDRESS,
    MAIL_FROM_NAME,
    MAIL_HOST,
    MAIL_MAILER,
    MAIL_MIN_INTERVAL,
    MAIL_PASSWORD,
    MAIL_PORT,
    MAIL_USERNAME,
)

log = logging.getLogger("kebiao.mail")

TIMEOUT = 20
RETRY_DELAYS = (10, 60)  # 秒
_QUEUE_MAX = 500


@dataclass
class Mail:
    to: str
    subject: str
    body: str


_queue: queue.Queue[Mail | None] = queue.Queue(maxsize=_QUEUE_MAX)
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()
_stop = threading.Event()


def mask(address: str) -> str:
    """nufewiki@163.com → n***i@163.com，日志里只认得出是哪个域。"""
    local, _, domain = (address or "").partition("@")
    if not domain:
        return "***"
    head = local[:1]
    tail = local[-1:] if len(local) > 2 else ""
    return f"{head}***{tail}@{domain}"


def build(mail: Mail) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((MAIL_FROM_NAME, MAIL_FROM_ADDRESS))
    msg["To"] = mail.to
    msg["Subject"] = mail.subject
    msg["Date"] = formatdate(localtime=True)
    domain = MAIL_FROM_ADDRESS.partition("@")[2] or "localhost"
    msg["Message-ID"] = make_msgid(domain=domain)
    msg["Auto-Submitted"] = "auto-generated"  # 让对方的自动回复别回给我们
    msg.set_content(mail.body)
    return msg


def _deliver_smtp(msg: EmailMessage) -> None:
    if MAIL_ENCRYPTION == "ssl":
        server = smtplib.SMTP_SSL(MAIL_HOST, MAIL_PORT, timeout=TIMEOUT, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(MAIL_HOST, MAIL_PORT, timeout=TIMEOUT)
    with server:
        if MAIL_ENCRYPTION in ("tls", "starttls"):
            server.starttls(context=ssl.create_default_context())
        if MAIL_USERNAME:
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
        server.send_message(msg)


def _deliver_log(msg: EmailMessage) -> None:
    log.info("[MAIL_MAILER=log] to=%s subject=%s\n%s", mask(msg["To"]), msg["Subject"], msg.get_content())


def deliver(msg: EmailMessage) -> None:
    """真正发出去的那一步；测试里替换它。"""
    (_deliver_log if MAIL_MAILER == "log" else _deliver_smtp)(msg)


def _worker() -> None:
    last_sent = 0.0
    while not _stop.is_set():
        try:
            mail = _queue.get(timeout=1)
        except queue.Empty:
            continue
        if mail is None:
            break
        msg = build(mail)
        for attempt in range(len(RETRY_DELAYS) + 1):
            wait = last_sent + MAIL_MIN_INTERVAL - time.monotonic()
            if wait > 0 and _stop.wait(wait):
                return
            try:
                deliver(msg)
                last_sent = time.monotonic()
                log.info("mail sent to=%s", mask(mail.to))
                break
            except (smtplib.SMTPException, OSError) as e:
                last_sent = time.monotonic()
                if isinstance(e, smtplib.SMTPRecipientsRefused | smtplib.SMTPAuthenticationError) \
                        or attempt == len(RETRY_DELAYS):
                    log.warning("mail to=%s dropped: %s", mask(mail.to), type(e).__name__)
                    break
                log.info("mail to=%s failed (%s), retrying", mask(mail.to), type(e).__name__)
                if _stop.wait(RETRY_DELAYS[attempt]):
                    return


def start() -> None:
    global _thread
    if not MAIL_ENABLED:
        return
    with _thread_lock:
        if _thread and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_worker, name="mailer", daemon=True)
        _thread.start()


def stop() -> None:
    _stop.set()
    with _thread_lock:
        thread = _thread
    if thread:
        with contextlib.suppress(queue.Full):
            _queue.put_nowait(None)
        thread.join(timeout=5)


def send(to: str, subject: str, body: str) -> bool:
    """入队；未配置邮件或队列满时返回 False（调用方不应因此失败）。"""
    if not MAIL_ENABLED:
        return False
    try:
        _queue.put_nowait(Mail(to, subject, body))
        return True
    except queue.Full:
        log.warning("mail queue full, dropping mail to=%s", mask(to))
        return False
