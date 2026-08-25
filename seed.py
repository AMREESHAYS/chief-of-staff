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


def scope_cache(fixture):
    """Scope items already extracted by the shipped provider, cached per
    fixture so iterating costs no API calls and no daily quota."""
    return fixture.with_name(fixture.stem + "_scope.json")


def run_snapshot(fixture):
    """A full pipeline run captured from the shipped provider, so work
    downstream of the classifier starts from real output for free."""
    return fixture.with_name(fixture.stem + "_run.json")


def load_fixture(path=FIXTURE):
    path = Path(path)
    data = json.loads(path.read_text())
    sow = (path.parent.parent / data["project"]["sow_filename"]).read_text()
    return data, sow


def to_mime(data, msg, subject):
    """One fixture message as RFC 822, for Gmail insert."""
    mail = EmailMessage()
    mail["To"] = data["owner_email"] if msg["from_counterparty"] else data["client_email"]
    mail["From"] = data["client_email"] if msg["from_counterparty"] else data["owner_email"]
    mail["Subject"] = subject
    mail["Date"] = format_datetime(datetime.fromisoformat(msg["at"]))
    mail.set_content(msg["body"])
    return base64.urlsafe_b64encode(mail.as_bytes()).decode()


def seed(conn, data, sow, gmail_ids=None, cache=None):
    now = datetime.now(timezone.utc).isoformat()
    p = data["project"]
    project_id = conn.execute(
        "INSERT INTO project (client_name, my_role, owner_tz, sow_filename,"
        " sow_text, created_at) VALUES (?,?,?,?,?,?)",
        (p["client_name"], p.get("my_role", "contractor"), p["owner_tz"],
         p["sow_filename"], sow, now),
    ).lastrowid

    thread_id = conn.execute(
        "INSERT INTO thread (project_id, gmail_thread_id, subject) VALUES (?,?,?)",
        (project_id, None, data["thread"]["subject"]),
    ).lastrowid

    for i, m in enumerate(data["messages"]):
        conn.execute(
            "INSERT INTO message (thread_id, gmail_msg_id, sender, from_counterparty,"
            " received_at, body) VALUES (?,?,?,?,?,?)",
            (
                thread_id,
                gmail_ids[i] if gmail_ids else None,
                data["client_email"] if m["from_counterparty"] else data["owner_email"],
                m["from_counterparty"],
                m["at"],
                m["body"],
            ),
        )
    if cache and cache.exists():
        conn.executemany(
            "INSERT INTO scope_item (project_id, item_text, source_quote,"
            " category) VALUES (?,?,?,?)",
            [(project_id, i["item_text"], i["source_quote"], i["category"])
             for i in json.loads(cache.read_text())],
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
    ap.add_argument("--fixture", default=FIXTURE, type=Path,
                    help="which thread fixture to load")
    ap.add_argument("--add", action="store_true",
                    help="keep existing projects and add this one alongside")
    ap.add_argument("--gmail", action="store_true", help="also insert into demo Gmail")
    ap.add_argument("--snapshot", action="store_true",
                    help="save the current verdicts and obligations to fixtures")
    ap.add_argument("--replay", action="store_true",
                    help="load a saved pipeline run instead of calling a model")
    args = ap.parse_args()

    fixture = Path(args.fixture)
    if args.snapshot:
        return snapshot(fixture)

    db.init()
    data, sow = load_fixture(fixture)
    gmail_ids = push_to_gmail(data, data["thread"]["subject"]) if args.gmail else None

    with db.connect() as conn:
        if not args.add:
            for table in ("action", "verdict", "obligation", "message",
                          "thread", "scope_item", "project"):
                conn.execute(f"DELETE FROM {table}")
        project_id = seed(conn, data, sow, gmail_ids, scope_cache(fixture))
        check(conn, project_id, data, sow)
        replayed = replay(conn, project_id, fixture) if args.replay else 0

    cache = scope_cache(fixture)
    cached = len(json.loads(cache.read_text())) if cache.exists() else 0
    print(f"seeded project {project_id}: {len(data['messages'])} messages, "
          f"{cached} cached scope items"
          + ("" if cached else " (run ingest.py to extract scope)")
          + (f", {replayed} replayed verdicts" if args.replay else ""))


def snapshot(fixture=FIXTURE, project_id=None):
    """Capture one project's run. Messages are keyed by arrival time — row ids
    change every reseed, timestamps do not."""
    with db.connect() as conn:
        if project_id is None:
            data, _ = load_fixture(fixture)
            project_id = conn.execute(
                "SELECT id FROM project WHERE client_name = ?",
                (data["project"]["client_name"],)).fetchone()[0]
        # scope item ids are global and shift on every reseed, exactly like
        # obligation ids. Stored raw, a replayed verdict cites whatever now
        # occupies that row — which can be a clause from a different client's
        # contract, rendered as a perfectly plausible citation.
        scope_at = {
            r["id"]: i
            for i, r in enumerate(conn.execute(
                "SELECT id FROM scope_item WHERE project_id = ? ORDER BY id",
                (project_id,)))
        }
        verdicts = [
            dict(r)
            for r in conn.execute(
                "SELECT m.received_at, v.label, v.scope_item_id, v.reasoning,"
                " v.confidence, v.references_obligation_id, v.obligation_relation,"
                " (SELECT received_at FROM message WHERE id = v.accepts_change_to)"
                "   AS accepts_change_at"
                " FROM verdict v JOIN message m ON m.id = v.message_id"
                " JOIN thread t ON t.id = m.thread_id"
                " WHERE t.project_id = ? ORDER BY m.received_at", (project_id,)
            )
        ]
        rows = conn.execute(
            "SELECT o.id, m.received_at, o.promise_text, o.due_phrase,"
            " o.due_at, o.due_confidence, o.status, o.owed_by FROM obligation o"
            " JOIN message m ON m.id = o.message_id"
            " WHERE o.project_id = ? ORDER BY o.id", (project_id,)
        ).fetchall()
        # obligation ids are global and shift as other projects are seeded, so
        # references travel as a position within this project's own list
        position = {r["id"]: i for i, r in enumerate(rows)}
        obligations = [{k: r[k] for k in r.keys() if k != "id"} for r in rows]

        for v in verdicts:
            v["references_obligation_at"] = position.get(
                v.pop("references_obligation_id"))
            v["scope_item_at"] = scope_at.get(v.pop("scope_item_id"))

        actions = []
        for a in conn.execute(
            "SELECT a.type, a.target_id, a.payload, a.state FROM action a"
            " ORDER BY a.id"
        ):
            a = dict(a)
            if a["type"] == "nudge":
                if a["target_id"] not in position:
                    continue
                a["target"] = position[a["target_id"]]
            else:
                hit = conn.execute(
                    "SELECT m.received_at FROM message m JOIN thread t"
                    " ON t.id = m.thread_id WHERE m.id = ? AND t.project_id = ?",
                    (a["target_id"], project_id)).fetchone()
                if not hit:
                    continue
                a["target"] = hit["received_at"]
            del a["target_id"]
            actions.append(a)

        # scope agreed by email is part of the run, not part of the contract
        amendments = [
            dict(r)
            for r in conn.execute(
                "SELECT s.item_text, s.source_quote, s.category, s.agreed_at,"
                " m.received_at FROM scope_item s"
                " JOIN message m ON m.id = s.origin_message_id"
                " WHERE s.project_id = ? AND s.origin = 'amendment'"
                " ORDER BY s.agreed_at", (project_id,))
        ]

    if not verdicts:
        # an empty run would silently overwrite a good capture with nothing
        raise SystemExit(
            f"refusing to snapshot: no verdicts for {fixture.name}."
            " Run the pipeline first, or --replay to restore one.")

    run_snapshot(fixture).write_text(
        json.dumps({"verdicts": verdicts, "obligations": obligations,
                    "actions": actions, "amendments": amendments},
                   indent=1, ensure_ascii=False)
    )
    print(f"snapshot: {len(verdicts)} verdicts, {len(obligations)} obligations,"
          f" {len(actions)} actions, {len(amendments)} amendments")


def replay(conn, project_id, fixture=FIXTURE):
    """Reload a captured run. Scope item ids are stable because seed inserts
    the cached items in file order; obligation ids are reassigned here."""
    saved_path = run_snapshot(fixture)
    if not saved_path.exists():
        return 0
    saved = json.loads(saved_path.read_text())
    # keyed by arrival time, carrying which side sent it: owed_by is derived
    # from the sender, never replayed from the snapshot. The same captured run
    # then reads correctly from either side of the contract.
    by_time = {
        r["received_at"]: (r["id"], r["from_counterparty"])
        for r in conn.execute(
            "SELECT m.id, m.received_at, m.from_counterparty FROM message m"
            " JOIN thread t ON t.id = m.thread_id WHERE t.project_id = ?",
            (project_id,)
        )
    }
    # positions in the snapshot's own list -> the ids this database issues now
    issued = [
        conn.execute(
            "INSERT INTO obligation (project_id, message_id, promise_text,"
            " due_phrase, due_at, due_confidence, status, created_at, owed_by)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (project_id, by_time[o["received_at"]][0], o["promise_text"],
             o["due_phrase"], o["due_at"], o["due_confidence"], o["status"],
             o["received_at"],
             "them" if by_time[o["received_at"]][1] else "me"),
        ).lastrowid
        for o in saved["obligations"]
    ]

    for a in saved.get("amendments", []):
        conn.execute(
            "INSERT INTO scope_item (project_id, item_text, source_quote,"
            " category, origin, origin_message_id, agreed_at)"
            " VALUES (?,?,?,?,'amendment',?,?)",
            (project_id, a["item_text"], a["source_quote"], a["category"],
             by_time[a["received_at"]][0], a["agreed_at"]))

    scope_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM scope_item WHERE project_id = ? ORDER BY id",
        (project_id,))]

    for v in saved["verdicts"]:
        at = v.get("references_obligation_at")
        cited = v.get("scope_item_at")
        conn.execute(
            "INSERT INTO verdict (message_id, label, scope_item_id, reasoning,"
            " confidence, references_obligation_id, obligation_relation,"
            " accepts_change_to) VALUES (?,?,?,?,?,?,?,?)",
            (by_time[v["received_at"]][0], v["label"],
             scope_ids[cited] if cited is not None else None,
             v["reasoning"], v["confidence"],
             issued[at] if at is not None else None,
             v["obligation_relation"],
             (by_time[v["accepts_change_at"]][0]
              if v.get("accepts_change_at") else None)),
        )

    for a in saved.get("actions", []):
        target = (issued[a["target"]] if a["type"] == "nudge"
                  else by_time[a["target"]][0])
        conn.execute(
            "INSERT INTO action (type, target_id, payload, state, created_at)"
            " VALUES (?,?,?,?,?)",
            (a["type"], target, a["payload"], a["state"],
             datetime.now(timezone.utc).isoformat()),
        )
    return len(saved["verdicts"])


def check(conn, project_id, data, sow):
    """Smallest thing that fails if the load broke."""
    rows = conn.execute(
        "SELECT m.body, m.from_counterparty, m.received_at FROM message m"
        " JOIN thread t ON t.id = m.thread_id WHERE t.project_id = ?"
        " ORDER BY m.received_at",
        (project_id,),
    ).fetchall()
    assert len(rows) == len(data["messages"]), f"{len(rows)} rows loaded"

    # ordering must survive the round trip — due-date anchoring depends on it
    assert [r["received_at"] for r in rows] == sorted(
        m["at"] for m in data["messages"]
    ), "messages out of order"

    # the SOW must contain the lines this thread's out_of_scope beats depend
    # on, or those citations later point at nothing. Which lines those are
    # belongs to the fixture, not to the seeder.
    for phrase in data.get("must_contain", []):
        assert phrase in sow, f"SOW missing: {phrase!r}"

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
