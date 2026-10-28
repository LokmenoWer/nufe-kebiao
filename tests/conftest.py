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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
