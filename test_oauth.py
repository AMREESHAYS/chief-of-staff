"""Checks for the Gmail connect flow.

Nothing here talks to Google. What is worth testing without it is the part that
protects the user: that a callback nobody started is refused, that the scopes
asked for are the two the product uses, and that disconnecting really removes
the token.

Run: .venv/bin/python test_oauth.py     (no API calls, no framework)
"""
import time

import oauth


def test_only_two_scopes_are_requested():
    """Scope creep in an OAuth request is the literal kind. Anything beyond
    read and draft has to be a deliberate decision, not a drift."""
    assert oauth.SCOPES == [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.compose",
    ]
    assert not any("gmail.send" in s for s in oauth.SCOPES)
    assert not any("mail.google.com" in s for s in oauth.SCOPES), \
        "the full-mailbox scope grants deletion"


def test_a_callback_nobody_started_is_refused():
    """Without this, a third party can trigger the callback and attach their
    own account."""
    try:
        oauth.finish("never-issued", "http://x/cb?code=1", "http://x/cb")
    except PermissionError as e:
        assert "did not start here" in str(e)
    else:
        raise AssertionError("an unsolicited callback was accepted")


def test_an_empty_state_is_refused():
    try:
        oauth.finish("", "http://x/cb?code=1", "http://x/cb")
    except PermissionError:
        pass
    else:
        raise AssertionError("a missing state was accepted")


def test_a_state_is_used_once():
    oauth._pending["single-use"] = time.time()
    try:
        oauth.finish("single-use", "http://x/cb?code=1", "http://x/cb")
    except Exception:
        pass                      # it fails later, at the network; that is fine
    assert "single-use" not in oauth._pending, "a state survived its use"


def test_a_stale_state_expires():
    oauth._pending["long-ago"] = time.time() - (oauth.STATE_TTL + 60)
    try:
        oauth.finish("long-ago", "http://x/cb?code=1", "http://x/cb")
    except PermissionError:
        pass
    else:
        raise AssertionError("a state older than its lifetime was accepted")


def test_disconnect_removes_the_token():
    import db

    db.init()
    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO account (email, token, scopes, connected_at)"
            " VALUES (?,?,?,?)",
            ("throwaway@example.com", '{"token": "x", "refresh_token": "y"}',
             " ".join(oauth.SCOPES), "2026-09-01T00:00:00Z"))

    assert any(a["email"] == "throwaway@example.com" for a in oauth.connected())
    oauth.disconnect("throwaway@example.com")
    assert not any(a["email"] == "throwaway@example.com"
                   for a in oauth.connected()), "the token outlived disconnect"


def test_listing_accounts_never_returns_the_token():
    """The connect page renders this list. A token must not be able to reach a
    template by accident."""
    import db

    db.init()
    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO account (email, token, scopes, connected_at)"
            " VALUES (?,?,?,?)",
            ("shown@example.com", '{"refresh_token": "SECRET-VALUE"}',
             " ".join(oauth.SCOPES), "2026-09-01T00:00:00Z"))
    try:
        for a in oauth.connected():
            assert "token" not in a, "the token is exposed to the page"
            assert "SECRET-VALUE" not in str(a)
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM account WHERE email = ?",
                         ("shown@example.com",))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nall checks passed")
