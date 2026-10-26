import hmac
import hashlib
import secrets
import time


def now() -> int:
    return int(time.time())


def token() -> str:
    return secrets.token_urlsafe(32)


def keyed_hash(value: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), value.encode(), hashlib.sha256).hexdigest()
