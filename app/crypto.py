"""落库敏感数据的加解密：AES-256-GCM，应用主密钥来自环境变量（可替换为 KMS 解出的数据密钥）。

每条记录随机 12 字节 nonce；AAD 绑定 "用途:用户id"，防止把 A 的密文挪到 B 的行上解出来。
所有解密失败（密钥轮换、数据损坏、AAD 不匹配）统一抛 CryptoError，调用方不必关心细节。
"""
from __future__ import annotations

import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import APP_SECRET_KEY

_VERSION = b"\x01"
_NONCE_LEN = 12
_aes = AESGCM(APP_SECRET_KEY)


class CryptoError(Exception):
    """密文无法解密：密钥变了、数据损坏，或密文被挪到了别的用途/用户上。"""


def encrypt(plaintext: bytes, purpose: str, user_id: int) -> bytes:
    nonce = os.urandom(_NONCE_LEN)
    return _VERSION + nonce + _aes.encrypt(nonce, plaintext, f"{purpose}:{user_id}".encode())


def decrypt(blob: bytes, purpose: str, user_id: int) -> bytes:
    if not isinstance(blob, bytes | bytearray | memoryview):
        raise CryptoError("ciphertext is not bytes")
    if len(blob) <= 1 + _NONCE_LEN or blob[:1] != _VERSION:
        raise CryptoError("unknown ciphertext format")
    try:
        return _aes.decrypt(bytes(blob[1:1 + _NONCE_LEN]), bytes(blob[1 + _NONCE_LEN:]),
                            f"{purpose}:{user_id}".encode())
    except Exception as e:  # InvalidTag / ValueError 等
        raise CryptoError("cannot decrypt ciphertext") from e


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
