"""离线演示：不访问学校服务器，用 examples/sample_timetable.json 跑完整前端。

    .venv/bin/python scripts/demo_server.py      # 打开 http://localhost:8765
    账号密码登录：任意学号 + 密码 demo；扫码登录：点"显示二维码"，几秒后自动"确认"。

数据库放在临时目录，退出即丢弃。
"""
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["APP_SECRET_KEY"] = base64.b64encode(os.urandom(32)).decode()
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "demo.db")
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["PUBLIC_BASE_URL"] = "http://localhost:8765"
sys.path.insert(0, str(ROOT))

from app import cas, jwxt  # noqa: E402

SAMPLE = json.loads((ROOT / "examples" / "sample_timetable.json").read_text("utf-8"))
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


class DemoFlow:
    """模拟 cas.LoginFlow：密码 demo 直接成功；扫码轮询几次后视为已确认。"""

    def __init__(self, session, username, password, device_id=None, debug_dir=None):
        self.session, self.username, self.password = session, username, password
        self.device_id = device_id or cas.new_device_id()
        self.stage, self.qr_status, self.reauth, self._polls = "init", "", {"phone": "138****0000"}, 0

    def wipe_password(self):
        self.password = ""

    def _done(self):
        self.session.cookies.set("CASTGC", "demo", domain=cas.AUTH_HOST, path="/authserver")
        self.stage = "done"
        return self.stage

    def start(self):
        if self.password != "demo":
            raise cas.CasError("演示模式：密码请填 demo")
        return self._done()

    def start_qr(self):
        self.stage, self.qr_status = "qr", "waiting"
        return self.stage

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
    return dict(SAMPLE)


cas.LoginFlow = DemoFlow
cas.login = lambda *a, **k: None
jwxt.fetch_timetable = demo_fetch

if __name__ == "__main__":
    import uvicorn

    from app.main import app
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
