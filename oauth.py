"""Connecting a Gmail account from the browser.

`auth.py` runs the desktop flow: it opens a browser on the machine running the
code and is fine for a developer. A person using this as a product cannot do
that, so this is the web flow — the user clicks Connect, consents on Google's
own page, and comes back.

What this does NOT do, deliberately:

  * it never sees or stores a password. Google collects the credential on its
    own domain and returns a token;
  * it asks for the two scopes the product actually uses, read and draft, and
    nothing else. There is no draft-only Gmail scope, so send-prevention stays
    where it has always been — there is no send path in this codebase;
  * it stores the refresh token server-side, never in a cookie or a URL.

The `state` parameter is a random value stored server-side and checked on the
way back. Without it, a third-party page can trigger the callback and attach
its own account to this session.
"""
import json
import time
from pathlib import Path

import db

HERE = Path(__file__).parent
CLIENT_SECRETS = HERE / "credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

# pending states, with the time they were issued. In-process is honest for a
# single-operator app; a multi-user deployment would put these in the database.
# ponytail: in-memory store, move to a table when there is more than one user
_pending = {}
STATE_TTL = 600           # ten minutes to finish a consent screen is plenty


def configured():
    """Whether an OAuth client has been set up at all."""
    return CLIENT_SECRETS.exists()


def _flow(redirect_uri):
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(
        str(CLIENT_SECRETS), scopes=SCOPES, redirect_uri=redirect_uri)
    return flow


def begin(redirect_uri):
    """Returns the Google URL to send the user to."""
    flow = _flow(redirect_uri)
    url, state = flow.authorization_url(
        access_type="offline",        # we need a refresh token
        include_granted_scopes="true",
        prompt="consent",             # so a re-connect returns a refresh token
    )
    _expire()
    _pending[state] = time.time()
    return url


def _expire():
    cutoff = time.time() - STATE_TTL
    for state in [s for s, issued in _pending.items() if issued < cutoff]:
        _pending.pop(state, None)


def finish(state, full_url, redirect_uri):
    """Exchange the code for a token. Raises if the state is not one we issued."""
    _expire()
    if not state or state not in _pending:
        raise PermissionError(
            "this sign-in did not start here, or it took too long")
    _pending.pop(state, None)

    flow = _flow(redirect_uri)
    flow.fetch_token(authorization_response=full_url)
    creds = flow.credentials

    email = _address_of(creds)
    store(email, creds)
    return email


def _address_of(creds):
    from googleapiclient.discovery import build

    profile = build("gmail", "v1", credentials=creds).users().getProfile(
        userId="me").execute()
    return profile["emailAddress"]


def store(email, creds):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO account (email, token, scopes, connected_at)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(email) DO UPDATE SET token = excluded.token,"
            " scopes = excluded.scopes, connected_at = excluded.connected_at",
            (email, creds.to_json(), " ".join(SCOPES), now))


def connected():
    """The accounts currently linked, without their tokens."""
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT email, scopes, connected_at FROM account ORDER BY email")]


def credentials_for(email):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    with db.connect() as conn:
        row = conn.execute("SELECT token FROM account WHERE email = ?",
                           (email,)).fetchone()
    if not row:
        return None
    creds = Credentials.from_authorized_user_info(json.loads(row["token"]), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        store(email, creds)
    return creds


def disconnect(email):
    """Forget the token. Also asks Google to revoke it, so the grant does not
    outlive the user's decision to remove it."""
    revoked = False
    try:
        creds = credentials_for(email)
        if creds and creds.token:
            import urllib.parse
            import urllib.request

            urllib.request.urlopen(
                "https://oauth2.googleapis.com/revoke?"
                + urllib.parse.urlencode({"token": creds.token}), timeout=8)
            revoked = True
    except Exception:
        # Withdrawing the grant at Google is best effort: the network may be
        # down, and a token stored in an unreadable shape cannot be sent
        # anywhere. Deleting it here is not optional either way — a person who
        # asks to disconnect must not be blocked by a broken token.
        revoked = False
    with db.connect() as conn:
        conn.execute("DELETE FROM account WHERE email = ?", (email,))
    return revoked
