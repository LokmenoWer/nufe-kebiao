"""落库敏感数据的加解密：AES-256-GCM，应用主密钥来自环境变量（可替换为 KMS 解出的数据密钥）。

每条记录随机 12 字节 nonce；AAD 绑定 "用途:用户id"，防止把 A 的密文挪到 B 的行上解出来。
"""
from __future__ import annotations

import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import APP_SECRET_KEY

_VERSION = b"\x01"
_aes = AESGCM(APP_SECRET_KEY)


def encrypt(plaintext: bytes, purpose: str, user_id: int) -> bytes:
    nonce = os.urandom(12)
    return _VERSION + nonce + _aes.encrypt(nonce, plaintext, f"{purpose}:{user_id}".encode())


def decrypt(blob: bytes, purpose: str, user_id: int) -> bytes:
    if blob[:1] != _VERSION:
        raise ValueError("unknown ciphertext version")
    return _aes.decrypt(blob[1:13], blob[13:], f"{purpose}:{user_id}".encode())


def encrypt_json(obj, purpose: str, user_id: int) -> bytes:
    return encrypt(json.dumps(obj).encode(), purpose, user_id)


def decrypt_json(blob: bytes, purpose: str, user_id: int):
    return json.loads(decrypt(blob, purpose, user_id))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
