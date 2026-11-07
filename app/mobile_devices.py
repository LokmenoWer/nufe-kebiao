"""安卓 App 的设备绑定：个人信息不随只读链接走，只给配对过的设备。

- App 在 Android Keystore 里生成不可导出的 P-256 密钥；网站发一次性配对码，App 凭它登记公钥。
  只拿到长期链接的人没有配对码，绑不上。
- 读个人信息、领手机代抓任务时用设备私钥签名（iOS 脚本没有 Keystore，用的是 crypto.widget_fetch_key）（token、设备 id、时间戳），服务器按公钥校验，时间差不超过 SIG_SKEW。
- 配对码只存哈希；撤销单个设备只影响个人信息，课表仍凭链接读取；重新生成 / 停用链接时全部设备失效。
"""
from __future__ import annotations

import base64
import binascii
import secrets

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_der_public_key

from . import crypto, db

PAIR_TTL = 600        # 秒：配对码有效期
SIG_SKEW = 300        # 秒：签名时间戳允许的误差
MAX_DEVICES = 5
NAME_MAX = 64


class PairError(Exception):
    """配对失败；消息直接给用户看。"""


class AuthError(Exception):
    """设备认证失败。skew 为 True 时是手机时钟不准，客户端可按 server_time 校正后重试。"""

    def __init__(self, skew: bool = False):
        super().__init__("device auth failed")
        self.skew = skew


def start_pairing(uid: int) -> str:
    """发一个新配对码，同一用户之前没用掉的作废。"""
    code = secrets.token_urlsafe(32)
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO mobile_pairings(user_id, code_hash, expires_at) VALUES(?,?,?)",
                  (uid, crypto.token_hash(code), db.now() + PAIR_TTL))
    return code


def _public_key_der(public_key: str) -> bytes:
    try:
        key = load_der_public_key(base64.b64decode(public_key, validate=True))
    except (ValueError, TypeError, binascii.Error) as e:
        raise PairError("设备公钥格式不对") from e
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise PairError("设备公钥格式不对")
    return key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def bind(uid: int, code: str, public_key: str, name: str) -> str:
    """配对码换设备 id。公钥不合法、设备超限时不消耗配对码；同一把公钥重新配对只留新记录。"""
    der = _public_key_der(public_key)
    device_id = secrets.token_urlsafe(16)
    now = db.now()
    with db.tx() as c:
        c.execute("DELETE FROM mobile_devices WHERE user_id=? AND public_key=?", (uid, der))
        count = c.execute("SELECT COUNT(*) FROM mobile_devices WHERE user_id=?", (uid,)).fetchone()[0]
        if count >= MAX_DEVICES:
            raise PairError(f"最多绑定 {MAX_DEVICES} 台设备，请先在网站上撤销不用的设备")
        used = c.execute("DELETE FROM mobile_pairings WHERE user_id=? AND code_hash=? AND expires_at>?",
                         (uid, crypto.token_hash(code), now)).rowcount
        if not used:
            raise PairError("配对链接已失效，请在网站上重新生成")
        c.execute("INSERT INTO mobile_devices(id, user_id, public_key, name, created_at, last_seen_at) "
                  "VALUES(?,?,?,?,?,?)", (device_id, uid, der, name.strip()[:NAME_MAX] or "安卓设备", now, now))
    return device_id


def bind_verified(uid: int, public_key: str, name: str) -> str:
    """App 2.x 托管时绑定设备：调用方刚用学校会话证实了是本人（见 service.enroll_from_app），不需要配对码。

    同一把公钥再次托管只换 id；超过上限时挤掉最久没用的那台，免得重装几次 App 就再也托管不上。
    """
    der = _public_key_der(public_key)
    device_id = secrets.token_urlsafe(16)
    now = db.now()
    with db.tx() as c:
        c.execute("DELETE FROM mobile_devices WHERE user_id=? AND public_key=?", (uid, der))
        c.execute("""DELETE FROM mobile_devices WHERE id IN (
                         SELECT id FROM mobile_devices WHERE user_id=? ORDER BY last_seen_at DESC LIMIT -1 OFFSET ?)""",
                  (uid, MAX_DEVICES - 1))
        c.execute("INSERT INTO mobile_devices(id, user_id, public_key, name, created_at, last_seen_at) "
                  "VALUES(?,?,?,?,?,?)", (device_id, uid, der, name.strip()[:NAME_MAX] or "安卓设备", now, now))
    return device_id


def signed_message(token: str, device_id: str, timestamp: str) -> bytes:
    return f"nufe-kebiao-device-v1\n{token}\n{device_id}\n{timestamp}".encode()


def authenticate(uid: int, token: str, device_id: str, timestamp: str, signature: str) -> None:
    """校验不过抛 AuthError。先验签再比时间：只有真正配对过的设备才会被告知要校正时钟。"""
    row = db.connect().execute("SELECT public_key FROM mobile_devices WHERE id=? AND user_id=?",
                               (device_id, uid)).fetchone()
    if not row:
        raise AuthError
    try:
        sent_at = int(timestamp)
        sig = base64.b64decode(signature, validate=True)
    except (ValueError, binascii.Error) as e:
        raise AuthError from e
    message = signed_message(token, device_id, timestamp)
    try:
        load_der_public_key(row["public_key"]).verify(sig, message, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as e:
        raise AuthError from e
    now = db.now()
    if abs(now - sent_at) > SIG_SKEW:
        raise AuthError(skew=True)
    with db.tx() as c:
        c.execute("UPDATE mobile_devices SET last_seen_at=? WHERE id=?", (now, device_id))


def list_devices(uid: int) -> list[dict]:
    rows = db.connect().execute(
        "SELECT id, name, created_at, last_seen_at FROM mobile_devices WHERE user_id=? ORDER BY created_at DESC",
        (uid,)).fetchall()
    return [dict(r) for r in rows]


def revoke(uid: int, device_id: str) -> bool:
    with db.tx() as c:
        return c.execute("DELETE FROM mobile_devices WHERE id=? AND user_id=?", (device_id, uid)).rowcount > 0


def clear(c, uid: int) -> None:
    """链接换了 / 停用：在调用方的事务里清掉所有设备和没用掉的配对码。"""
    c.execute("DELETE FROM mobile_devices WHERE user_id=?", (uid,))
    c.execute("DELETE FROM mobile_pairings WHERE user_id=?", (uid,))
