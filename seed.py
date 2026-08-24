"""Load the demo project + client thread.

Default: into SQLite, for the dev loop.
--gmail: also insert the same messages into the demo Gmail account so the demo
runs against the live API. Uses users.messages.insert, which files a message
without sending it — no mail leaves the account.

The correspondents are fictional (.example addresses, invented client). Do not
point this at a real client's mail.
"""
import argparse
import base64
import json
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

import db

FIXTURE = Path(__file__).parent / "fixtures" / "thread.json"
# scope items already extracted by the shipped provider, cached so that
# iterating on later stages costs no API calls and no daily quota
SCOPE_CACHE = Path(__file__).parent / "fixtures" / "scope_items.json"


def load_fixture(path=FIXTURE):
    data = json.loads(path.read_text())
    sow = (path.parent.parent / data["project"]["sow_filename"]).read_text()
    return data, sow


def to_mime(data, msg, subject):
    """One fixture message as RFC 822, for Gmail insert."""
    mail = EmailMessage()
    mail["To"] = data["owner_email"] if msg["from_client"] else data["client_email"]
    mail["From"] = data["client_email"] if msg["from_client"] else data["owner_email"]
    mail["Subject"] = subject
    mail["Date"] = format_datetime(datetime.fromisoformat(msg["at"]))
    mail.set_content(msg["body"])
    return base64.urlsafe_b64encode(mail.as_bytes()).decode()


def seed(conn, data, sow, gmail_ids=None):
    now = datetime.now(timezone.utc).isoformat()
    p = data["project"]
    project_id = conn.execute(
        "INSERT INTO project (client_name, owner_tz, sow_filename, sow_text,"
        " created_at) VALUES (?,?,?,?,?)",
        (p["client_name"], p["owner_tz"], p["sow_filename"], sow, now),
    ).lastrowid

    thread_id = conn.execute(
        "INSERT INTO thread (project_id, gmail_thread_id, subject) VALUES (?,?,?)",
        (project_id, None, data["thread"]["subject"]),
    ).lastrowid

    for i, m in enumerate(data["messages"]):
        conn.execute(
            "INSERT INTO message (thread_id, gmail_msg_id, sender, from_client,"
            " received_at, body) VALUES (?,?,?,?,?,?)",
            (
                thread_id,
                gmail_ids[i] if gmail_ids else None,
                data["client_email"] if m["from_client"] else data["owner_email"],
                m["from_client"],
                m["at"],
                m["body"],
            ),
        )
    if SCOPE_CACHE.exists():
        conn.executemany(
            "INSERT INTO scope_item (project_id, item_text, source_quote,"
            " category) VALUES (?,?,?,?)",
            [(project_id, i["item_text"], i["source_quote"], i["category"])
             for i in json.loads(SCOPE_CACHE.read_text())],
        )
    return project_id


def push_to_gmail(data, subject):
    """Insert fixture messages into the demo account. Returns Gmail message ids."""
    import auth

    svc = auth.gmail(scopes=auth.SEED_SCOPES)
    ids = []
    for m in data["messages"]:
        res = (
            svc.users()
            .messages()
            .insert(
                userId="me",
                internalDateSource="dateHeader",
                body={"raw": to_mime(data, m, subject), "labelIds": ["INBOX"]},
            )
            .execute()
        )
        ids.append(res["id"])
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gmail", action="store_true", help="also insert into demo Gmail")
    args = ap.parse_args()

    db.init()
    data, sow = load_fixture()
    gmail_ids = push_to_gmail(data, data["thread"]["subject"]) if args.gmail else None

    with db.connect() as conn:
        conn.execute("DELETE FROM message")
        conn.execute("DELETE FROM thread")
        conn.execute("DELETE FROM scope_item")
        conn.execute("DELETE FROM project")
        project_id = seed(conn, data, sow, gmail_ids)
        check(conn, project_id, data, sow)

    cached = len(json.loads(SCOPE_CACHE.read_text())) if SCOPE_CACHE.exists() else 0
    print(f"seeded project {project_id}: {len(data['messages'])} messages, "
          f"{cached} cached scope items"
          + ("" if cached else " (run ingest.py to extract scope)"))


def check(conn, project_id, data, sow):
    """Smallest thing that fails if the load broke."""
    rows = conn.execute(
        "SELECT m.body, m.from_client, m.received_at FROM message m"
        " JOIN thread t ON t.id = m.thread_id WHERE t.project_id = ?"
        " ORDER BY m.received_at",
        (project_id,),
    ).fetchall()
    assert len(rows) == len(data["messages"]), f"{len(rows)} rows loaded"

    # ordering must survive the round trip — due-date anchoring depends on it
    assert [r["received_at"] for r in rows] == sorted(
        m["at"] for m in data["messages"]
    ), "messages out of order"

    # the SOW must actually contain the exclusions the demo turns on, or every
    # out_of_scope citation later has nothing verbatim to point at
    for excluded in ("e-commerce functionality are excluded", "multi-language"):
        assert excluded in sow, f"SOW missing exclusion: {excluded}"

    # a cached quote that no longer matches the SOW would citate nothing —
    # same rule as ingest, enforced when the cache is loaded rather than trusted
    import ingest
    items = conn.execute(
        "SELECT source_quote FROM scope_item WHERE project_id = ?", (project_id,)
    ).fetchall()
    stale = [r["source_quote"] for r in items
             if ingest._norm(r["source_quote"]) not in ingest._norm(sow)]
    assert not stale, f"cached scope quotes no longer in the SOW: {stale}"

    stored = conn.execute(
        "SELECT sow_text FROM project WHERE id = ?", (project_id,)
    ).fetchone()["sow_text"]
    assert stored == sow, "SOW text mangled on write"


if __name__ == "__main__":
    main()
