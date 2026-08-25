"""Obligation ledger — what you promised, and whether it's late.

Due dates are resolved in code, not by the model. The classifier copies the
words ("by Friday") and this module turns them into a date, anchored to when
the message arrived, in the project owner's timezone.

Two reasons it works this way. A model asked for a date will invent a
plausible one; copied words can be checked against the message. And the anchor
is the arrival time, not today — "by Friday" said three weeks ago meant that
Friday.

The timezone is not decoration. A message sent 20:00 UTC arrives after
midnight in Asia/Kolkata, so "tomorrow" is two calendar days later in UTC than
a naive reading gives. An off-by-one due date reads as a broken ledger.

A promise with no resolvable date is not a failure to record. "Soon" and "I'll
look into it" are the commitments that go missing, so they are stored with a
null due date and status 'vague' — visible, uncounted, un-chased.
"""
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# words that promise a time without naming one
VAGUE = (
    "soon", "shortly", "asap", "as soon as", "at some point", "later",
    "eventually", "in a bit", "next sprint", "when i can", "when i get",
    "in due course", "sometime",
)

# strip the preposition, keep the date
LEADING = re.compile(r"^(by|before|on|no later than|latest|until|till)\s+")

END_OF_WEEK = ("end of this week", "end of the week", "end of week",
               "this week", "end of the working week")


def _end_of_day(date, tz):
    """Due means close of business on that day, in the owner's timezone."""
    return (
        datetime.combine(date, time(23, 59, 59), tzinfo=ZoneInfo(tz))
        .astimezone(ZoneInfo("UTC"))
        .isoformat()
    )


def _friday_of_week(date):
    """Friday of the week containing `date`. Weekend rolls to the next one."""
    if date.weekday() > 4:
        return date + timedelta(days=7 - date.weekday() + 4)
    return date + timedelta(days=4 - date.weekday())


def resolve_due(phrase, received_at, tz="Asia/Kolkata"):
    """(due_at_utc_iso, confidence) — or (None, None) when no date is stated.

    `received_at` is the message's arrival time in UTC ISO format.
    """
    if not phrase:
        return None, None

    p = LEADING.sub("", phrase.strip().lower().rstrip(".!?"))
    if any(v in p for v in VAGUE):
        return None, None

    anchor = datetime.fromisoformat(received_at.replace("Z", "+00:00")).astimezone(
        ZoneInfo(tz)
    )
    today = anchor.date()

    if "day after tomorrow" in p:
        return _end_of_day(today + timedelta(days=2), tz), 0.9
    if "tomorrow" in p:
        return _end_of_day(today + timedelta(days=1), tz), 0.95
    if "today" in p or "end of day" in p or "tonight" in p:
        return _end_of_day(today, tz), 0.95

    if any(w in p for w in END_OF_WEEK):
        return _end_of_day(_friday_of_week(today), tz), 0.8
    if "next week" in p:
        # genuinely ambiguous — a date is more useful than nothing, but say so
        return _end_of_day(_friday_of_week(today) + timedelta(days=7), tz), 0.5
    if "end of the month" in p or "end of month" in p:
        first_next = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        return _end_of_day(first_next - timedelta(days=1), tz), 0.8

    days = re.search(r"in (\d+) days?", p)
    if days:
        return _end_of_day(today + timedelta(days=int(days.group(1))), tz), 0.9

    for name, index in WEEKDAYS.items():
        if name in p:
            ahead = (index - today.weekday()) % 7  # today counts as "by Friday"
            return _end_of_day(today + timedelta(days=ahead), tz), 0.9

    # a written-out date, e.g. "25 August"
    try:
        from dateutil import parser

        parsed = parser.parse(p, default=anchor, fuzzy=True).date()
    except (ValueError, OverflowError, ImportError):
        return None, None
    # a resolved date before the message was sent means we misread the words
    return (_end_of_day(parsed, tz), 0.7) if parsed >= today else (None, None)


def record(conn, project_id, message, verdict, tz):
    """Store a promise, tagged with who made it.

    Both directions are kept. A freelancer mostly wants their own promises
    back; a shop that hired one mostly wants the vendor's. The same thread
    answers both questions, and which one matters is the reader's side, not a
    property of the message.
    """
    if not verdict.promise_text:
        return None

    due_at, confidence = resolve_due(verdict.due_phrase, message["received_at"], tz)
    return conn.execute(
        "INSERT INTO obligation (project_id, message_id, promise_text,"
        " due_phrase, due_at, due_confidence, status, created_at, owed_by)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (
            project_id,
            message["id"],
            verdict.promise_text,
            verdict.due_phrase,
            due_at,
            confidence,
            "vague" if due_at is None else "open",
            message["received_at"],
            "them" if message["from_counterparty"] else "me",
        ),
    ).lastrowid


def open_obligations(conn, project_id, as_of):
    """Everything still outstanding when `as_of` arrived — the classifier can
    only reference commitments that already existed."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT id, promise_text, due_phrase, due_at, status, created_at,"
            " owed_by FROM obligation"
            " WHERE project_id = ? AND status IN ('open','overdue','vague')"
            " AND created_at < ? ORDER BY id",
            (project_id, as_of),
        )
    ]


def _due_label(o, tz="UTC"):
    if o["due_at"]:
        return local_day(o["due_at"], tz)
    return f"NO DATE (said {o['due_phrase'] or 'nothing'})"


def render(obligations, tz="UTC"):
    if not obligations:
        return "(none)"
    return "\n".join(
        f"[{o['id']}] ({'they' if o.get('owed_by') == 'them' else 'you'} promised)"
        f" \"{o['promise_text']}\" — {local_day(o['created_at'], tz)},"
        f" due {_due_label(o, tz)}"
        for o in obligations
    )


def local_day(iso, tz):
    """Render an instant as the calendar day it falls on where the developer
    lives. Due dates are stored end-of-day UTC so comparisons are correct;
    read back in UTC they land on the following date for any zone west of it.
    """
    return (
        datetime.fromisoformat(iso.replace("Z", "+00:00"))
        .astimezone(ZoneInfo(tz))
        .strftime("%-d %b")
    )


def close(conn, obligation_id):
    """A promise that was kept. Only ever called for the developer's own
    messages — the client confirming receipt is not delivery."""
    conn.execute(
        "UPDATE obligation SET status = 'done' WHERE id = ? AND status != 'done'",
        (obligation_id,),
    )


def sweep(conn, project_id, now):
    """Mark past-due open obligations overdue. `now` is explicit so the demo
    can be replayed at the thread's own end date."""
    return conn.execute(
        "UPDATE obligation SET status = 'overdue' WHERE project_id = ?"
        " AND status = 'open' AND due_at IS NOT NULL AND due_at < ?",
        (project_id, now),
    ).rowcount
