import base64
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["APP_SECRET_KEY"] = base64.b64encode(os.urandom(32)).decode()
os.environ["DB_PATH"] = str(Path(_tmp) / "test.db")
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["PUBLIC_BASE_URL"] = "http://testserver"
os.environ["MAIL_MAILER"] = "log"  # 邮件功能打开，但测试里只进内存 outbox，不真的发
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
