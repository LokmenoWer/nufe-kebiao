"""落库敏感数据的加解密：AES-256-GCM，应用主密钥来自环境变量（可替换为 KMS 解出的数据密钥）。

每条记录随机 12 字节 nonce；AAD 绑定 "用途:用户id"，防止把 A 的密文挪到 B 的行上解出来。
所有解密失败（密钥轮换、数据损坏、AAD 不匹配）统一抛 CryptoError，调用方不必关心细节。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import APP_SECRET_KEY, CREDENTIAL_SECRET_KEY

log = logging.getLogger("kebiao.crypto")

_VERSION = b"\x01"
_ENVELOPE_VERSION = b"\x02"
_NONCE_LEN = 12
_DEK_LEN = 32
_WRAPPED_DEK_LEN = _DEK_LEN + 16  # 32-byte DEK + 16-byte GCM tag
_aes = AESGCM(APP_SECRET_KEY)


class CryptoError(Exception):
    """密文无法解密：密钥变了、数据损坏，或密文被挪到了别的用途/用户上。"""


def encrypt(plaintext: bytes, purpose: str, user_id: int) -> bytes:
    if purpose == "password":
        return encrypt_password(plaintext, user_id)
    nonce = os.urandom(_NONCE_LEN)
    return _VERSION + nonce + _aes.encrypt(nonce, plaintext, f"{purpose}:{user_id}".encode())


def decrypt(blob: bytes, purpose: str, user_id: int) -> bytes:
    if purpose == "password":
        return decrypt_password(blob, user_id)
    if not isinstance(blob, bytes | bytearray | memoryview):
        raise CryptoError("ciphertext is not bytes")
    if len(blob) <= 1 + _NONCE_LEN or blob[:1] != _VERSION:
        raise CryptoError("unknown ciphertext format")
    try:
        return _aes.decrypt(bytes(blob[1:1 + _NONCE_LEN]), bytes(blob[1 + _NONCE_LEN:]),
                            f"{purpose}:{user_id}".encode())
    except Exception as e:  # InvalidTag / ValueError 等
        raise CryptoError("cannot decrypt ciphertext") from e


def _derive_credential_kek(user_id: int, student_id: str, salt: bytes) -> bytes:
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=f"kebiao-cred-kek-v2:{user_id}:{student_id}".encode(),
    )
    return hkdf.derive(CREDENTIAL_SECRET_KEY)


def encrypt_password(plaintext: bytes, user_id: int, student_id: str = "") -> bytes:
    """信封加密（Envelope Encryption）：每条记录生成独立随机 DEK，由 HKDF 派生的独立 KEK 封装。"""
    salt = os.urandom(16)
    kek_nonce = os.urandom(_NONCE_LEN)
    dek_nonce = os.urandom(_NONCE_LEN)
    dek = os.urandom(_DEK_LEN)
    kek = _derive_credential_kek(user_id, student_id, salt)
    aad = f"password-v2:{user_id}:{student_id}".encode()
    wrapped_dek = AESGCM(kek).encrypt(kek_nonce, dek, aad)
    ciphertext = AESGCM(dek).encrypt(dek_nonce, plaintext, aad)
    return _ENVELOPE_VERSION + salt + kek_nonce + dek_nonce + wrapped_dek + ciphertext


def decrypt_password(blob: bytes, user_id: int, student_id: str = "") -> bytes:
    if not isinstance(blob, bytes | bytearray | memoryview):
        raise CryptoError("ciphertext is not bytes")
    raw = bytes(blob)
    header_len = 1 + 16 + _NONCE_LEN + _NONCE_LEN + _WRAPPED_DEK_LEN
    if len(raw) <= header_len or raw[:1] != _ENVELOPE_VERSION:
        raise CryptoError("unknown credential envelope format")
    salt = raw[1:17]
    kek_nonce = raw[17:17 + _NONCE_LEN]
    dek_nonce = raw[17 + _NONCE_LEN:17 + 2 * _NONCE_LEN]
    wrapped_dek = raw[17 + 2 * _NONCE_LEN:header_len]
    ciphertext = raw[header_len:]
    aad = f"password-v2:{user_id}:{student_id}".encode()
    try:
        kek = _derive_credential_kek(user_id, student_id, salt)
        dek = AESGCM(kek).decrypt(kek_nonce, wrapped_dek, aad)
        plaintext = AESGCM(dek).decrypt(dek_nonce, ciphertext, aad)
    except Exception as e:
        raise CryptoError("cannot decrypt credential envelope") from e
    log.info("credential decrypted for background renewal: user_id=%s", user_id)
    return plaintext


def migrate_legacy_password(blob: bytes, user_id: int, student_id: str = "") -> bytes | None:
    """将旧版 v1 (\\x01) 格式的 enc_password 迁移为 v2 信封加密格式。"""
    if not isinstance(blob, bytes | bytearray | memoryview):
        return None
    raw = bytes(blob)
    if len(raw) <= 1 + _NONCE_LEN or raw[:1] != _VERSION:
        return None
    try:
        plaintext = _aes.decrypt(raw[1:1 + _NONCE_LEN], raw[1 + _NONCE_LEN:], f"password:{user_id}".encode())
    except Exception:
        return None
    return encrypt_password(plaintext, user_id, student_id)


def encrypt_json(obj, purpose: str, user_id: int) -> bytes:
    return encrypt(json.dumps(obj).encode(), purpose, user_id)


def decrypt_json(blob: bytes, purpose: str, user_id: int):
    try:
        return json.loads(decrypt(blob, purpose, user_id))
    except CryptoError:
        raise
    except (ValueError, TypeError) as e:
        raise CryptoError("decrypted payload is not valid JSON") from e


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def widget_fetch_key(token: str) -> str:
    """iOS 脚本领代抓任务用的密钥：由主密钥和小组件 token 算出，不落库，重新生成 / 停用链接后自然失效。

    光有 token 算不出来（token_hash 不带密钥，这里必须用 HMAC），所以链接泄露不等于密钥泄露。
    """
    mac = hmac.new(APP_SECRET_KEY, b"kebiao-widget-fetch-v1\n" + token.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()
