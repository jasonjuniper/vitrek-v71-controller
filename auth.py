"""
auth.py
-------
Two-role access control for the Juniper Test Station.

THE MODEL
---------
  operator (default)  No login. The station boots into this role and stays
                      there. An operator can connect the instrument using the
                      settings an admin already saved, run a saved test
                      sequence, abort it, and read results.

  admin               Password login. Everything the operator can do, plus
                      creating and editing test sequences, changing connection
                      and rig settings, and running PVD baselines.

The default role is deliberately passwordless. A shop-floor operator running a
qualification sequence should never be blocked by a forgotten password, and a
login prompt on the run screen would only teach people to share credentials.
What needs protecting is the *definition* of a test, not the running of one.

PASSWORD STORAGE
----------------
Only a PBKDF2-SHA256 verifier is stored, in the app_settings table:

    pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>

The password itself is never written to the database, the config, or a log.
Per Juniper practice the password belongs in 1Password; this app only ever
holds something it can check a guess against.

Comparison uses hmac.compare_digest, so a wrong guess takes the same time to
reject regardless of how many leading characters happened to be right.

FIRST RUN
---------
With no admin password configured, admin_configured() is False and the UI
offers to set one. Until that happens the station is operator-only — it does
NOT silently fall open to admin, which would be the obvious wrong default.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from functools import wraps

from flask import jsonify, session

import database as db

# ── Tunables ──────────────────────────────────────────────────────────────────

PBKDF2_ITERATIONS = 600_000     # OWASP guidance for PBKDF2-HMAC-SHA256
SALT_BYTES = 16

# An admin session drops back to operator after this long without a request.
# The station sits unattended on a bench; an admin who walks away should not
# leave the settings unlocked behind them.
ADMIN_IDLE_TIMEOUT_S = 30 * 60

ROLE_OPERATOR = "operator"
ROLE_ADMIN = "admin"

_ADMIN_HASH_KEY = "admin_password_hash"
_SECRET_KEY_KEY = "flask_secret_key"


# ── Password hashing ──────────────────────────────────────────────────────────

def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Return a self-describing PBKDF2-SHA256 verifier string."""
    if not password:
        raise ValueError("Password must not be empty")
    salt = secrets.token_bytes(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """
    Check a password against a stored verifier.

    Returns False rather than raising on a malformed or missing verifier — a
    corrupted settings row should lock admin out, not crash the login route.
    """
    if not password or not stored:
        return False
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(iters))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


# ── Admin credential management ───────────────────────────────────────────────

def admin_configured(db_path: str = db.DB_PATH) -> bool:
    return bool(db.get_setting(_ADMIN_HASH_KEY, db_path=db_path))


def set_admin_password(password: str, db_path: str = db.DB_PATH) -> None:
    """Set or replace the admin password. Stores only the verifier."""
    if len(password or "") < 8:
        raise ValueError("Admin password must be at least 8 characters")
    db.set_setting(_ADMIN_HASH_KEY, hash_password(password), db_path=db_path)


def check_admin_password(password: str, db_path: str = db.DB_PATH) -> bool:
    return verify_password(password, db.get_setting(_ADMIN_HASH_KEY, db_path=db_path) or "")


def generate_password(length: int = 24) -> str:
    """
    Generate a strong password for storing in 1Password.

    Deliberately excludes characters that get mangled when a password is read
    aloud across a workshop or pasted through a terminal.
    """
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def get_or_create_secret_key(db_path: str = db.DB_PATH) -> bytes:
    """
    Flask session signing key, persisted so a restart does not silently log
    everyone out mid-shift — and so it is not regenerated per worker process.
    """
    existing = db.get_setting(_SECRET_KEY_KEY, db_path=db_path)
    if existing:
        return bytes.fromhex(existing)
    key = secrets.token_bytes(32)
    db.set_setting(_SECRET_KEY_KEY, key.hex(), db_path=db_path)
    return key


# ── Session role ──────────────────────────────────────────────────────────────

def current_role() -> str:
    """
    The caller's effective role, honouring the admin idle timeout.

    The timeout is enforced on read rather than by a background sweep, so a
    stale session can never be used even once after it expires.
    """
    if session.get("role") != ROLE_ADMIN:
        return ROLE_OPERATOR
    last = session.get("admin_seen_at", 0)
    if time.time() - last > ADMIN_IDLE_TIMEOUT_S:
        session.pop("role", None)
        session.pop("admin_seen_at", None)
        return ROLE_OPERATOR
    session["admin_seen_at"] = time.time()
    return ROLE_ADMIN


def is_admin() -> bool:
    return current_role() == ROLE_ADMIN


def login_admin(password: str, db_path: str = db.DB_PATH) -> bool:
    if not check_admin_password(password, db_path=db_path):
        return False
    session["role"] = ROLE_ADMIN
    session["admin_seen_at"] = time.time()
    session.permanent = False
    return True


def logout_admin() -> None:
    session.pop("role", None)
    session.pop("admin_seen_at", None)


def auth_status(db_path: str = db.DB_PATH) -> dict:
    role = current_role()
    return {
        "role": role,
        "is_admin": role == ROLE_ADMIN,
        "admin_configured": admin_configured(db_path=db_path),
        "idle_timeout_s": ADMIN_IDLE_TIMEOUT_S,
    }


# ── Route decorator ───────────────────────────────────────────────────────────

def admin_required(fn):
    """
    Refuse the request unless the session is an authenticated admin.

    403 rather than 401: the operator is a legitimate, fully authenticated
    identity here. They are not unauthenticated, they are unauthorised.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_admin():
            return jsonify({
                "ok": False,
                "error": "Admin access required. Log in as admin to change this.",
                "role": current_role(),
            }), 403
        return fn(*args, **kwargs)
    return wrapper
