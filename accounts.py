"""Accounts, passwords, and sessions.

The password is never stored and never logged. What is kept is a scrypt hash
and the random salt it used. scrypt is deliberately slow and memory-hard, so a
stolen database does not become a list of passwords — which matters more here
than in most products, because the thing behind the login is somebody's client
correspondence and their contracts.

The session cookie holds a random token; the database holds only its hash. A
leaked database therefore does not hand anyone a live session either.

Everything a signed-in person can reach is scoped by their user id. That is not
a nicety: this project has already found the same bug four times in a smaller
form, where an id space was assumed to be scoped and was not. Between customers
the same mistake means one person reads another's contracts.
"""
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone

import db

# scrypt parameters. n is the work factor; 2**15 takes roughly a tenth of a
# second here, which is slow enough to matter to an attacker and fast enough
# that nobody notices signing in.
# maxmem must be set explicitly: n=2**15 with r=8 needs about 32MB, and
# OpenSSL refuses anything over its own default cap of exactly that.
SCRYPT = dict(n=2 ** 15, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)

SESSION_DAYS = 30
COOKIE = "chief_session"

MIN_PASSWORD = 10          # length beats complexity rules; see the note below


class Problem(Exception):
    """Something the person can fix, phrased for them rather than for a log."""


def _now():
    return datetime.now(timezone.utc)


def _stamp(when):
    return when.replace(microsecond=0).isoformat()


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode("utf-8"),
                            salt=bytes.fromhex(salt), **SCRYPT)
    return salt, digest.hex()


def verify(password, salt, expected):
    """Constant-time comparison: a timing difference leaks how much of a hash
    an attacker has guessed."""
    _, actual = hash_password(password, salt)
    return hmac.compare_digest(actual, expected)


def check_new_password(password):
    """Length is the property that actually resists guessing. A short password
    full of punctuation is weaker than a long ordinary one, so this asks for
    length and refuses the handful of strings everybody tries first."""
    if len(password) < MIN_PASSWORD:
        raise Problem(f"Use at least {MIN_PASSWORD} characters. "
                      "A few ordinary words are stronger than a short password "
                      "with symbols in it.")
    if password.lower() in {"password12", "1234567890", "qwertyuiop",
                            "letmein123", "chiefofstaff"}:
        raise Problem("That is one of the first passwords anyone would try.")


def create(username, password, display_name=None):
    username = (username or "").strip()
    if len(username) < 3:
        raise Problem("Pick a username of at least three characters.")
    check_new_password(password)

    salt, digest = hash_password(password)
    with db.connect() as conn:
        taken = conn.execute(
            "SELECT 1 FROM user WHERE username = ?", (username,)).fetchone()
        if taken:
            raise Problem("That username is taken.")
        return conn.execute(
            "INSERT INTO user (username, salt, password_hash, display_name,"
            " created_at) VALUES (?,?,?,?,?)",
            (username, salt, digest, display_name or username,
             _stamp(_now()))).lastrowid


def authenticate(username, password):
    """Returns the user id, or None. Deliberately says nothing about which half
    was wrong, and spends the same time either way so that a missing username
    cannot be told apart from a wrong password by how fast it fails."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, salt, password_hash FROM user WHERE username = ?",
            ((username or "").strip(),)).fetchone()
    if not row:
        # hash anyway, against a throwaway salt, so both paths cost the same
        hash_password(password or "", secrets.token_hex(16))
        return None
    return row["id"] if verify(password or "", row["salt"],
                               row["password_hash"]) else None


def start_session(user_id):
    """Returns the cookie value. Only its hash is stored."""
    token = secrets.token_urlsafe(32)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO session (token_hash, user_id, created_at, expires_at)"
            " VALUES (?,?,?,?)",
            (hashlib.sha256(token.encode()).hexdigest(), user_id,
             _stamp(_now()), _stamp(_now() + timedelta(days=SESSION_DAYS))))
    return token


def user_for(token):
    """The signed-in user, or None. Expired sessions are removed as they are
    met, so a stale row cannot come back to life."""
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT s.user_id, s.expires_at, u.username, u.display_name,"
            " u.onboarded_at FROM session s JOIN user u ON u.id = s.user_id"
            " WHERE s.token_hash = ?", (token_hash,)).fetchone()
        if not row:
            return None
        if row["expires_at"] < _stamp(_now()):
            conn.execute("DELETE FROM session WHERE token_hash = ?",
                         (token_hash,))
            return None
    return {"id": row["user_id"], "username": row["username"],
            "display_name": row["display_name"],
            "onboarded_at": row["onboarded_at"]}


def end_session(token):
    if not token:
        return
    with db.connect() as conn:
        conn.execute("DELETE FROM session WHERE token_hash = ?",
                     (hashlib.sha256(token.encode()).hexdigest(),))


def mark_onboarded(user_id):
    with db.connect() as conn:
        conn.execute("UPDATE user SET onboarded_at = ? WHERE id = ?",
                     (_stamp(_now()), user_id))


def projects_of(conn, user_id):
    """Every project this person owns, and only those."""
    return [dict(r) for r in conn.execute(
        "SELECT id, client_name FROM project WHERE user_id = ? ORDER BY id",
        (user_id,))]


def owns(conn, user_id, project_id):
    """Whether this person may see this project at all. Called before anything
    is loaded, not after."""
    return conn.execute(
        "SELECT 1 FROM project WHERE id = ? AND user_id = ?",
        (project_id, user_id)).fetchone() is not None
