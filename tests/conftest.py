import base64
import hashlib
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
# 管理员密码 "admin-test-pw"；测试里用很少的迭代次数，免得每次登录都算半秒
_salt = os.urandom(16)
os.environ["ADMIN_PASSWORD_HASH"] = "pbkdf2_sha256$1000${}${}".format(
    base64.b64encode(_salt).decode(),
    base64.b64encode(hashlib.pbkdf2_hmac("sha256", b"admin-test-pw", _salt, 1000)).decode())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
