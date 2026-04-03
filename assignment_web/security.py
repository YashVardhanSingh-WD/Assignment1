from functools import wraps
import hashlib
import hmac
import os
import secrets

from flask import redirect, request, session, url_for


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 390_000)
    return f"{salt.hex()}:{digest.hex()}"


def verify_password(stored_value: str, password: str) -> bool:
    try:
        salt_hex, digest_hex = stored_value.split(":", 1)
    except ValueError:
        return False

    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(digest_hex)
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 390_000)
    return hmac.compare_digest(expected, candidate)


def generate_reset_code(length: int = 6) -> str:
    digits = "0123456789"
    return "".join(secrets.choice(digits) for _ in range(length))


def hash_reset_code(secret_key: str, worker_id: int, code: str) -> str:
    message = f"{worker_id}:{code}".encode("utf-8")
    return hmac.new(secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_reset_code(secret_key: str, worker_id: int, code: str, stored_hash: str) -> bool:
    candidate = hash_reset_code(secret_key, worker_id, code)
    return hmac.compare_digest(candidate, stored_hash)


def worker_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if "worker_id" not in session:
            return redirect(url_for("worker_login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapped_view


def owner_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not session.get("owner_authenticated"):
            return redirect(url_for("owner_login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapped_view
