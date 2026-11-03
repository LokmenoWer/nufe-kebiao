"""离线演示：不访问学校服务器，用 examples/sample_timetable.json 跑完整前端。

    .venv/bin/python scripts/demo_server.py      # 打开 http://localhost:8765
    账号密码登录：任意学号 + 密码 demo；扫码登录：点"显示二维码"，几秒后自动"确认"。
    想看人机验证的转发界面，把密码换成：
      captcha → 图形验证码（填 abcd）   slider → 滑块拼图（真的要拖到缺口）
      sms     → 短信二次认证（任意 6 位） otp → 动态口令二次认证（任意 6 位）
    邮件通知在演示模式下不真的发信：绑定邮箱的验证码会打印在这个终端里。
    服务器管理员页：http://localhost:8765/admin ，密码 demo。

数据库放在临时目录，退出即丢弃。
"""
import base64
import hashlib
import json
import os
import random
import struct
import sys
import tempfile
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["APP_SECRET_KEY"] = base64.b64encode(os.urandom(32)).decode()
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "demo.db")
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["PUBLIC_BASE_URL"] = "http://localhost:8765"
os.environ["MAIL_MAILER"] = "log"  # 邮件通知：不真的发信，验证码等内容打印在服务器日志里
_salt = os.urandom(16)  # 管理员页密码 demo
os.environ["ADMIN_PASSWORD_HASH"] = "pbkdf2_sha256$1000${}${}".format(
    base64.b64encode(_salt).decode(), base64.b64encode(hashlib.pbkdf2_hmac("sha256", b"demo", _salt, 1000)).decode())
sys.path.insert(0, str(ROOT))

from app import cas, jwxt, security  # noqa: E402

# 演示模式不限制登录次数（否则反复试前端很容易撞到真实部署的防撞库限制）
security.login_by_ip = security.RateLimiter(10_000, 900)
security.login_by_student = security.RateLimiter(10_000, 900)

SAMPLE = json.loads((ROOT / "examples" / "sample_timetable.json").read_text("utf-8"))
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _png(w, h, pixel):
    """纯 Python 写一张 RGBA PNG，演示用（不依赖 Pillow）。"""
    rows = (b"\x00" + bytes(v for x in range(w) for v in pixel(x, y)) for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 6)) + chunk(b"IEND", b""))


# 与学校一致：背景 590×360，拼图条 93×360，方块在条内横向偏移 16px
SLIDER_BG_W, SLIDER_H, SLIDER_PIECE_W, SLIDER_SQ, SLIDER_INSET = 590, 360, 93, 60, 16


def _demo_slider():
    hole_x = random.randint(200, 480)
    hole_y = random.randint(60, SLIDER_H - SLIDER_SQ - 60)

    def bg_color(x, y):
        return (60 + x * 140 // SLIDER_BG_W, 110 + y * 100 // SLIDER_H, 200 - x * 80 // SLIDER_BG_W, 255)

    def bg(x, y):
        r, g, b, a = bg_color(x, y)
        if hole_x <= x < hole_x + SLIDER_SQ and hole_y <= y < hole_y + SLIDER_SQ:
            return (r // 3, g // 3, b // 3, 255)
        return (r, g, b, a)

    def piece(x, y):
        sx = x - SLIDER_INSET
        if 0 <= sx < SLIDER_SQ and hole_y <= y < hole_y + SLIDER_SQ:
            r, g, b, _ = bg_color(hole_x + sx, y)
            edge = sx in (0, SLIDER_SQ - 1) or y in (hole_y, hole_y + SLIDER_SQ - 1)
            return (255, 255, 255, 255) if edge else (r, g, b, 255)
        return (0, 0, 0, 0)

    return hole_x, _png(SLIDER_BG_W, SLIDER_H, bg), _png(SLIDER_PIECE_W, SLIDER_H, piece)


class DemoFlow:
    """模拟 cas.LoginFlow：密码 demo 直接成功；扫码轮询几次后视为已确认；特殊密码演示各种人机验证。"""

    can_trust_device = True

    def __init__(self, session, username, password, device_id=None, debug_dir=None):
        self.session, self.username, self.password = session, username, password
        self.device_id = device_id or cas.new_device_id()
        self.stage, self.qr_status, self.reauth, self._polls = "init", "", {"phone": "138****0000"}, 0
        self.slider_key, self._hole_x = "", 0

    def wipe_password(self):
        self.password = ""

    def _done(self):
        # 和学校一样：账号密码登录（rememberMe）的 CASTGC 能用半年，扫码登录只给会话级的
        self.session.cookies.set("CASTGC", "demo", domain=cas.AUTH_HOST, path="/authserver",
                                 expires=int(time.time()) + 180 * 86400 if self.username else None)
        self.stage = "done"
        return self.stage

    def start(self):
        if self.password not in ("demo", "captcha", "slider", "sms", "otp"):
            raise cas.CasError("演示模式：密码请填 demo（或 captcha / slider / sms / otp 演示人机验证）")
        self.session.kb_demo_user = self.username  # 演示：把输入的学号当作当前用户
        if self.password in ("captcha", "slider"):
            self.stage = "captcha" if self.password == "captcha" else "slider"
            return self.stage
        return self._after_password()

    def _after_password(self):
        if self.password in ("sms", "otp"):
            self.reauth["method"] = self.password
            self.stage = "mfa"
            return self.stage
        return self._done()

    def captcha_image(self):
        return PNG, "image/png"

    def submit(self, captcha):
        if self.stage == "captcha" and captcha.lower() != "abcd":
            raise cas.CasError("演示模式：验证码填 abcd")
        return self._after_password()

    def slider_challenge(self):
        self._hole_x, big, small = _demo_slider()
        self.slider_key = "demo-key"
        return {"big": (big, "image/png"), "small": (small, "image/png")}

    def submit_slider(self, canvas_length, move_length, tracks):
        if not self.slider_key:
            raise cas.CasError("滑块验证已失效，请重新拖动")
        self.slider_key = ""
        target = (self._hole_x - SLIDER_INSET) * canvas_length / SLIDER_BG_W
        if abs(move_length - target) > 5:
            raise cas.CasError("拼图没有对齐，请再试一次")
        return self._after_password()

    def start_qr(self):
        self.stage, self.qr_status = "qr", "waiting"
        return self.stage

    def send_sms(self):
        self.sms_ready_at = time.time() + 60
        return "演示模式：验证码已发送（随便填 6 位数字）"

    def verify_sms(self, code, trust_device=True):
        return self._done()

    def qr_image(self):
        return PNG, "image/png"

    def refresh_qr(self):
        self._polls, self.qr_status = 0, "waiting"

    def poll_qr(self):
        self._polls += 1
        if self._polls >= 4:
            return self._done()
        if self._polls >= 2:
            self.qr_status = "scanned"
        return self.stage


def demo_fetch(session, semester_id=None, page_html=None):
    data = dict(SAMPLE)
    student = dict(SAMPLE.get("student") or {})
    # 演示模式：任意学号都能登录，学号以用户输入为准（扫码时退回示例学号）
    student["code"] = getattr(session, "kb_demo_user", None) or student.get("code") or "2020000000"
    data["student"] = student
    # 让切换学期在演示模式下也有数据可看（真实环境由教务系统按 id 返回）
    if semester_id:
        sid = int(semester_id)
        data["semester_id"] = sid
        name = next((s["name"] for s in SAMPLE.get("semesters", []) if s["id"] == sid), None)
        data["semester_name"] = name or str(sid)
    return data


cas.LoginFlow = DemoFlow
cas.login = lambda *a, **k: None
jwxt.fetch_timetable = demo_fetch


def seed_admin_demo():
    """给管理员页塞几个假用户，看得到排队、等手机、失败和设备这些状态。全是虚构数据。"""
    from app import db
    db.init()
    now = time.time()
    # (学号, 姓名, 状态, 上次成功距今, 手机代抓成功距今, 代抓失败次数, 绑定的设备)
    users = [
        ("2099000101", "张三", "ok", 600, 600, 0, "Xiaomi 2312DRA50C"),
        ("2099000102", "李四", "ok", 6 * 3600, 3600, 0, "HUAWEI ALN-AL00"),
        ("2099000103", "王五", "ok", 9 * 3600, None, 0, None),
        ("2099000104", "赵六", "error", 12 * 3600, 90000, 3, "OnePlus PJZ110"),
        ("2099000105", "钱七", "needs_rebind", 3 * 86400, None, 0, None),
    ]
    with db.tx() as c:
        for n, (sid, name, status, ok_ago, fetch_ago, fails, device) in enumerate(users):
            uid = c.execute(
                """INSERT INTO users(student_id, name, created_at, status, fail_count, last_error, last_success_at,
                                     last_seen_at, next_refresh_at, widget_token_hash, widget_fetch_ok_at,
                                     widget_fetch_fails)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, name, now - 30 * 86400, status, 1 if status == "error" else 0,
                 "学校服务器暂时无法访问" if status == "error" else None, now - ok_ago, now - 3600,
                 now + 900 if status == "error" else None, f"demo-{n}" if n < 4 else None,
                 now - fetch_ago if fetch_ago else None, fails)).lastrowid
            if device:
                c.execute("INSERT INTO mobile_devices(id, user_id, public_key, name, created_at, last_seen_at) "
                          "VALUES(?,?,?,?,?,?)", (f"demo-{n}", uid, b"demo", device, now - 7 * 86400, now - ok_ago))
            for k, (trigger, ok) in enumerate((("widget", 1), ("scheduled", 1), ("widget", 1), ("scheduled", n != 3))):
                c.execute("INSERT INTO refresh_log(user_id, at, trigger, ok, message) VALUES(?,?,?,?,?)",
                          (uid, now - (k + 1) * 1800 - n * 60, trigger, ok, "演示数据" if ok else "学校服务器暂时无法访问"))


if __name__ == "__main__":
    import uvicorn

    from app.main import app
    seed_admin_demo()
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
