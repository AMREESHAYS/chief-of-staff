"""Checks for accounts, passwords, sessions, and isolation between customers.

The isolation tests are the reason this file exists. This project has found the
same bug four times in a smaller form — an id space assumed to be scoped when
it was global. Between paying customers, that mistake means one person reads
another's contracts and client correspondence, and no unit test of the pipeline
would notice.

Run: .venv/bin/python test_accounts.py     (no API calls, no framework)
"""
import pathlib
import sqlite3

import accounts
import db

TEST_DB = pathlib.Path("/tmp/chief-accounts-test.db")


def fresh():
    TEST_DB.unlink(missing_ok=True)
    db.DB_PATH = TEST_DB
    db.init(TEST_DB)
    # db.connect() defaults to DB_PATH, which we have just repointed
    return TEST_DB


def make_user(username, password="correct horse battery"):
    return accounts.create(username, password)


def own_project(user_id, name):
    with db.connect() as conn:
        return conn.execute(
            "INSERT INTO project (user_id, client_name, my_role, owner_tz,"
            " sow_text, created_at) VALUES (?,?,?,?,?,?)",
            (user_id, name, "contractor", "UTC", "a contract",
             "2026-09-01T00:00:00Z")).lastrowid


# --- passwords -----------------------------------------------------------

def test_the_password_is_never_stored():
    fresh()
    make_user("alice", "a long enough passphrase")
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM user WHERE username = 'alice'").fetchone()
    stored = " ".join(str(v) for v in dict(row).values())
    assert "a long enough passphrase" not in stored, "the password is in the row"


def test_the_same_password_hashes_differently_for_two_people():
    """Per-user salt. Without it, identical passwords are visibly identical in
    the database and one cracked hash breaks every account that shares it."""
    fresh()
    make_user("bob", "the same passphrase")
    make_user("carol", "the same passphrase")
    with db.connect() as conn:
        rows = conn.execute("SELECT salt, password_hash FROM user").fetchall()
    assert rows[0]["salt"] != rows[1]["salt"]
    assert rows[0]["password_hash"] != rows[1]["password_hash"]


def test_the_right_password_authenticates():
    fresh()
    uid = make_user("dave", "a long enough passphrase")
    assert accounts.authenticate("dave", "a long enough passphrase") == uid


def test_a_wrong_password_does_not():
    fresh()
    make_user("erin", "a long enough passphrase")
    assert accounts.authenticate("erin", "a long enough passphras") is None
    assert accounts.authenticate("erin", "") is None


def test_an_unknown_username_returns_nothing_rather_than_raising():
    fresh()
    assert accounts.authenticate("nobody", "whatever at all") is None


def test_a_short_password_is_refused():
    fresh()
    try:
        accounts.create("frank", "short")
    except accounts.Problem as e:
        assert "characters" in str(e)
    else:
        raise AssertionError("a five character password was accepted")


def test_a_username_cannot_be_taken_twice():
    fresh()
    make_user("grace")
    try:
        make_user("GRACE")          # case-insensitive, or two people collide
    except accounts.Problem:
        pass
    else:
        raise AssertionError("the same username was issued twice")


# --- sessions ------------------------------------------------------------

def test_a_session_identifies_its_owner():
    fresh()
    uid = make_user("heidi")
    token = accounts.start_session(uid)
    assert accounts.user_for(token)["id"] == uid


def test_the_raw_token_is_not_in_the_database():
    """A leaked database must not hand anybody a live session."""
    fresh()
    token = accounts.start_session(make_user("ivan"))
    with db.connect() as conn:
        stored = conn.execute("SELECT token_hash FROM session").fetchone()[0]
    assert stored != token
    assert token not in stored


def test_an_invented_token_is_refused():
    fresh()
    make_user("judy")
    assert accounts.user_for("not-a-real-token") is None
    assert accounts.user_for("") is None
    assert accounts.user_for(None) is None


def test_signing_out_kills_the_session():
    fresh()
    token = accounts.start_session(make_user("ken"))
    accounts.end_session(token)
    assert accounts.user_for(token) is None


def test_an_expired_session_is_refused_and_removed():
    fresh()
    uid = make_user("laura")
    token = accounts.start_session(uid)
    import hashlib

    with db.connect() as conn:
        conn.execute("UPDATE session SET expires_at = '2020-01-01T00:00:00+00:00'"
                     " WHERE token_hash = ?",
                     (hashlib.sha256(token.encode()).hexdigest(),))
    assert accounts.user_for(token) is None
    with db.connect() as conn:
        left = conn.execute(
            "SELECT COUNT(*) FROM session WHERE token_hash = ?",
            (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()[0]
    assert left == 0, "the expired row survived"


# --- isolation between customers -----------------------------------------

def test_one_customer_cannot_see_anothers_contracts():
    fresh()
    a, b = make_user("anna"), make_user("brian")
    own_project(a, "Anna's client")
    own_project(b, "Brian's client")

    with db.connect() as conn:
        seen = [p["client_name"] for p in accounts.projects_of(conn, a)]
    assert seen == ["Anna's client"], f"Anna can see {seen}"


def test_guessing_a_project_id_is_not_enough():
    """The id is a small integer. Ownership has to be checked, not assumed
    from the fact that somebody typed it into the URL."""
    fresh()
    a, b = make_user("anita"), make_user("bruno")
    mine = own_project(a, "Anita's client")
    theirs = own_project(b, "Bruno's client")

    with db.connect() as conn:
        assert accounts.owns(conn, a, mine)
        assert not accounts.owns(conn, a, theirs), \
            "one customer was granted another's contract by id"
        assert not accounts.owns(conn, a, 9999)


def test_a_project_with_no_owner_belongs_to_nobody():
    """Rows predating accounts must not become everybody's."""
    fresh()
    a = make_user("orphan-check")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO project (client_name, my_role, owner_tz, sow_text,"
            " created_at) VALUES (?,?,?,?,?)",
            ("unowned", "contractor", "UTC", "x", "2026-09-01T00:00:00Z"))
        assert accounts.projects_of(conn, a) == []
        orphan = conn.execute(
            "SELECT id FROM project WHERE client_name = 'unowned'").fetchone()[0]
        assert not accounts.owns(conn, a, orphan)


if __name__ == "__main__":
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_"):
                fn()
                print(f"ok  {name}")
        print("\nall checks passed")
    finally:
        TEST_DB.unlink(missing_ok=True)
