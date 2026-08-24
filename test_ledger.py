"""Checks for due-date resolution and the ledger's filters.

Every case below is anchored to a real message time from the demo thread. An
off-by-one here doesn't crash — it shows a wrong date on screen, which is
worse.

Run: .venv/bin/python test_ledger.py     (no API calls, no framework)
"""
from types import SimpleNamespace

import ledger

TZ = "Asia/Kolkata"

# (message sent at UTC, phrase, expected due date in owner tz, note)
CASES = [
    # the demo's own promises
    ("2026-08-12T13:20:00Z", "by Friday", "2026-08-14", "Wed -> that Friday"),
    ("2026-08-05T05:20:00Z", "tomorrow", "2026-08-06", "staging link"),
    ("2026-08-18T06:50:00Z", "end of this week", "2026-08-21", "Tue -> Friday"),
    ("2026-08-03T06:10:00Z", "this week", "2026-08-07", "Mon -> Friday"),

    # weekday edges
    ("2026-08-14T06:00:00Z", "by Friday", "2026-08-14", "said on Friday = today"),
    ("2026-08-15T06:00:00Z", "by Friday", "2026-08-21", "Sat -> next Friday"),
    ("2026-08-22T06:00:00Z", "end of the week", "2026-08-28", "Sat rolls forward"),

    # other forms
    ("2026-08-10T05:00:00Z", "in 3 days", "2026-08-13", None),
    ("2026-08-10T05:00:00Z", "end of the month", "2026-08-31", None),
    ("2026-08-10T05:00:00Z", "day after tomorrow", "2026-08-12", None),
    ("2026-08-10T05:00:00Z", "before Wednesday", "2026-08-12", "preposition stripped"),
]

# phrases that must NOT produce a date
VAGUE_CASES = [
    ("2026-08-20T15:10:00Z", "soon"),          # the demo's vague commitment
    ("2026-08-20T15:10:00Z", "shortly"),
    ("2026-08-20T15:10:00Z", "asap"),
    ("2026-08-20T15:10:00Z", "at some point"),
    ("2026-08-20T15:10:00Z", "when I can"),
    ("2026-08-20T15:10:00Z", None),            # promise with no time at all
    ("2026-08-20T15:10:00Z", ""),
]


def local_date(iso):
    """The stored UTC instant, read back as a date in the owner's timezone."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return (
        datetime.fromisoformat(iso).astimezone(ZoneInfo(TZ)).date().isoformat()
    )


def test_phrases_resolve_to_the_right_day():
    for sent, phrase, expected, note in CASES:
        due, confidence = ledger.resolve_due(phrase, sent, TZ)
        assert due is not None, f"{phrase!r} produced no date"
        got = local_date(due)
        assert got == expected, (
            f"{phrase!r} sent {sent} -> {got}, expected {expected}"
            + (f" ({note})" if note else "")
        )
        assert 0 < confidence <= 1


def test_vague_phrases_produce_no_date():
    for sent, phrase in VAGUE_CASES:
        due, confidence = ledger.resolve_due(phrase, sent, TZ)
        assert due is None, f"{phrase!r} invented the date {due}"
        assert confidence is None


def test_timezone_shifts_the_anchor_day():
    # 20:00 UTC on the 12th is 01:30 on the 13th in Kolkata, so "tomorrow"
    # is the 14th locally. Resolving in UTC would say the 13th — the exact
    # off-by-one that makes a ledger look broken on camera.
    due, _ = ledger.resolve_due("tomorrow", "2026-08-12T20:00:00Z", TZ)
    assert local_date(due) == "2026-08-14"

    due_utc, _ = ledger.resolve_due("tomorrow", "2026-08-12T20:00:00Z", "UTC")
    assert due_utc[:10] == "2026-08-13", "UTC anchor should differ — that's the point"


def test_due_is_end_of_day_not_midnight():
    # a promise "by Friday" is not broken at 00:01 on Friday
    due, _ = ledger.resolve_due("by Friday", "2026-08-12T13:20:00Z", TZ)
    from datetime import datetime
    from zoneinfo import ZoneInfo

    local = datetime.fromisoformat(due).astimezone(ZoneInfo(TZ))
    assert (local.hour, local.minute) == (23, 59)


def test_a_date_in_the_past_is_rejected():
    # misreading words into a date before the message was sent means we got
    # it wrong; better no date than a date that is instantly overdue
    due, _ = ledger.resolve_due("last Tuesday", "2026-08-20T05:00:00Z", TZ)
    assert due is None or local_date(due) >= "2026-08-20"


def test_ambiguous_next_week_is_marked_lower_confidence():
    due, confidence = ledger.resolve_due("next week", "2026-08-10T05:00:00Z", TZ)
    assert due is not None
    assert confidence <= 0.5, "next week is a guess and should say so"


# --- ledger filters ------------------------------------------------------

def verdict(promise=None, phrase=None):
    return SimpleNamespace(promise_text=promise, due_phrase=phrase)


class FakeConn:
    def __init__(self):
        self.rows = []

    def execute(self, sql, params):
        self.rows.append(params)
        return SimpleNamespace(lastrowid=len(self.rows), rowcount=0)


def test_client_promises_are_not_tracked():
    # "Perfect, will look tomorrow" is the client's promise, not the
    # developer's obligation
    conn = FakeConn()
    msg = {"id": 1, "from_client": 1, "received_at": "2026-08-05T09:02:00Z"}
    assert ledger.record(conn, 1, msg, verdict("will look", "tomorrow"), TZ) is None
    assert conn.rows == []


def test_developer_promises_are_tracked():
    conn = FakeConn()
    msg = {"id": 2, "from_client": 0, "received_at": "2026-08-12T13:20:00Z"}
    assert ledger.record(conn, 1, msg,
                         verdict("Collections page", "by Friday"), TZ) == 1
    assert conn.rows[0][6] == "open"


def test_a_message_with_no_promise_writes_nothing():
    conn = FakeConn()
    msg = {"id": 3, "from_client": 0, "received_at": "2026-08-12T13:20:00Z"}
    assert ledger.record(conn, 1, msg, verdict(None, None), TZ) is None
    assert conn.rows == []


def test_vague_promise_is_stored_not_dropped():
    # the whole point of the vague bucket: recorded, visible, uncounted
    conn = FakeConn()
    msg = {"id": 4, "from_client": 0, "received_at": "2026-08-20T15:10:00Z"}
    ledger.record(conn, 1, msg, verdict("look at the load time", "soon"), TZ)
    project_id, message_id, promise, phrase, due_at, conf, status, created = conn.rows[0]
    assert due_at is None and status == "vague"
    assert phrase == "soon", "the words must survive for the UI to quote them"


def test_render_names_the_missing_date():
    text = ledger.render([
        {"id": 7, "promise_text": "load time", "due_phrase": "soon",
         "due_at": None, "status": "vague", "created_at": "2026-08-20T15:10:00Z"},
    ])
    assert "NO DATE" in text and "soon" in text and "[7]" in text


def test_render_handles_an_empty_ledger():
    assert ledger.render([]) == "(none)"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nall checks passed")
