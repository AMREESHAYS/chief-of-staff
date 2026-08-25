"""Drafts — the acting half, with the safety rails that make acting safe.

Three rules, enforced in code rather than trusted to the prompt:

1. Nothing is ever sent. There is no send path in this module. Gmail drafts are
   created through users.drafts().create and stop there. The Gmail scope grant
   would permit sending; the code does not.

2. A change-order reply must quote the contract line it relies on, verbatim,
   inside the message the client will read. A draft that cites nothing is a
   claim the client has no way to check.

3. The agent never invents a figure or a delivery date. Fees and percentages
   must already appear in the SOW. A late promise is acknowledged with the date
   that was missed — which is known — and leaves a placeholder for the new one,
   because committing the developer to a date they have not agreed to is the
   one thing an assistant must never do on their behalf.

Every proposal lands in the `action` table as 'proposed'. Nothing reaches Gmail
until a human approves it, and undo is free precisely because nothing was sent.
"""
import json
import re
import sys
from datetime import datetime, timezone

from pydantic import BaseModel

import db
import llm
from ingest import _norm

# a placeholder the developer must fill in — never a date the agent chose
DATE_PLACEHOLDER = "[NEW DATE]"

# money and percentages, the figures worth refusing to invent
# the digit-anchored tail matters: [\d,]+ would swallow a trailing comma, so
# "INR 85,000," in the contract would not match "INR 85,000" in a draft
FIGURE = re.compile(
    r"(?:(?:INR|Rs\.?|₹|\$)\s*[\d,]*\d(?:\.\d+)?|\b\d+(?:\.\d+)?\s*%)",
    re.IGNORECASE,
)

CHANGE_ORDER_SYSTEM = """You draft replies for a freelance developer writing to \
their client. The developer is not a lawyer and does not want to sound like \
one. Keep it short, warm, and matter-of-fact.

The client has asked for work that the signed contract does not cover. Draft \
the developer's reply.

The reply must:
- acknowledge the request without dismissing it — this is a client they want \
to keep
- quote the relevant line of the contract word for word, exactly as given to \
you, so the client can check it
- say plainly that the work sits outside the current agreement
- offer the next step: a written change order with a separate price
- stay under 120 words

The reply must NOT:
- quote a price, a fee, a percentage, or a discount. You do not know what this \
work is worth. Say the price will follow in the change order.
- promise a delivery date
- apologise for the contract. It was agreed by both sides."""

NUDGE_SYSTEM = f"""You draft short proactive updates from a freelance developer \
to their client about work that is running late.

The developer promised something by a date, that date has passed, and the \
client has not been told. Draft the update the developer should have sent.

The update must:
- name what was promised and the date that was missed
- be brief and direct. No long apology, no excuses.
- write the literal text {DATE_PLACEHOLDER} where a new delivery date belongs
- stay under 80 words

The update must NOT:
- state, guess, or imply any new date. You do not know when the developer can \
deliver, and committing them to a date they have not chosen is not yours to \
do. Use {DATE_PLACEHOLDER} and nothing else.
- quote any fee, price, or percentage."""


class Draft(BaseModel):
    body: str
    quoted_contract_text: str | None  # verbatim SOW span used, null for nudges


class UnsafeDraft(Exception):
    """A draft broke one of the rules above. Never silently repaired."""


def invented_figures(body, sow_text):
    """Figures in the draft that do not appear in the contract."""
    allowed = {f.lower().replace(" ", "") for f in FIGURE.findall(sow_text)}
    return [f for f in FIGURE.findall(body)
            if f.lower().replace(" ", "") not in allowed]


def validate_change_order(draft, sow_text):
    bad = invented_figures(draft.body, sow_text)
    if bad:
        raise UnsafeDraft(f"draft invents figures not in the contract: {bad}")

    quote = draft.quoted_contract_text
    if not quote:
        raise UnsafeDraft("change-order reply cites no contract text")
    if _norm(quote) not in _norm(sow_text):
        raise UnsafeDraft(f"cited text is not in the contract: {quote!r}")
    if _norm(quote) not in _norm(draft.body):
        raise UnsafeDraft(
            "the contract line is cited but does not appear in the message the "
            "client would read"
        )
    return draft


def validate_nudge(draft, sow_text):
    bad = invented_figures(draft.body, sow_text)
    if bad:
        raise UnsafeDraft(f"draft invents figures not in the contract: {bad}")
    if DATE_PLACEHOLDER not in draft.body:
        raise UnsafeDraft(
            f"nudge must leave {DATE_PLACEHOLDER} for the developer rather "
            "than commit them to a date"
        )
    return draft


def _generate(system, turns, validate, parse_fn, retries=1):
    """Generate, validate, and on a rail violation say exactly which rail was
    hit before trying again. Same shape as ingest: name the offence, do not
    ask vaguely for another go."""
    parse_fn = parse_fn or llm.parse
    for attempt in range(retries + 1):
        result = parse_fn(system, turns, Draft)
        try:
            return validate(result.parsed)
        except UnsafeDraft as e:
            if attempt == retries:
                raise
            turns = turns + [
                {"role": "assistant", "text": result.parsed.model_dump_json()},
                {"role": "user",
                 "text": f"That draft was rejected: {e}\n\nWrite it again, "
                         "fixing exactly that."},
            ]


def change_order(message, scope_item, sow_text, parse_fn=None):
    turns = [{
        "role": "user",
        "text": f"<client_message>\n{message['body']}\n</client_message>\n\n"
                f"<contract_line>\n{scope_item['source_quote']}\n</contract_line>"
                f"\n\nThis line is why the request is out of scope. Quote it "
                "word for word in your reply.",
    }]
    return _generate(CHANGE_ORDER_SYSTEM, turns,
                     lambda d: validate_change_order(d, sow_text), parse_fn)


def nudge(obligation, sow_text, parse_fn=None):
    turns = [{
        "role": "user",
        "text": f"<promise>\n{obligation['promise_text']}\n</promise>\n"
                f"<promised_on>{obligation['created_at'][:10]}</promised_on>\n"
                f"<was_due>{obligation['due_at'][:10]}</was_due>\n\n"
                "Draft the update.",
    }]
    return _generate(NUDGE_SYSTEM, turns,
                     lambda d: validate_nudge(d, sow_text), parse_fn)


def propose(conn, kind, target_id, draft):
    """Record a proposal. State starts at 'proposed' — a human moves it on."""
    return conn.execute(
        "INSERT INTO action (type, target_id, payload, state, created_at)"
        " VALUES (?,?,?,?,?)",
        (kind, target_id,
         json.dumps({"body": draft.body,
                     "quoted_contract_text": draft.quoted_contract_text}),
         "proposed", datetime.now(timezone.utc).isoformat()),
    ).lastrowid


def flag(conn, message_id, reason):
    """An unsure verdict produces no draft. It produces a question."""
    return conn.execute(
        "INSERT INTO action (type, target_id, payload, state, created_at)"
        " VALUES (?,?,?,?,?)",
        ("flag", message_id, json.dumps({"reason": reason}), "proposed",
         datetime.now(timezone.utc).isoformat()),
    ).lastrowid


def approve(conn, action_id):
    """Proposed or previously discarded — both can be approved. An action that
    has already reached Gmail is not re-approved here; undo it first."""
    conn.execute("UPDATE action SET state = 'approved' WHERE id = ? AND"
                 " state IN ('proposed', 'undone')", (action_id,))


def push(conn, action_id, service):
    """Create the Gmail draft for an approved action.

    users.drafts().create only. This module contains no call that sends mail,
    and that is the guarantee — the granted scope would allow sending.
    """
    row = conn.execute(
        "SELECT type, payload, state FROM action WHERE id = ?", (action_id,)
    ).fetchone()
    if row["state"] != "approved":
        raise ValueError(f"action {action_id} is {row['state']}, not approved")

    import base64
    from email.message import EmailMessage

    payload = json.loads(row["payload"])
    mail = EmailMessage()
    mail.set_content(payload["body"])
    created = service.users().drafts().create(
        userId="me",
        body={"message": {"raw": base64.urlsafe_b64encode(mail.as_bytes()).decode()}},
    ).execute()

    payload["gmail_draft_id"] = created["id"]
    conn.execute(
        "UPDATE action SET state = 'executed', payload = ?, executed_at = ?"
        " WHERE id = ?",
        (json.dumps(payload), datetime.now(timezone.utc).isoformat(), action_id),
    )
    return created["id"]


def undo(conn, action_id, service=None):
    """Undo costs nothing because nothing was sent — at worst a draft is
    deleted from the developer's own drafts folder."""
    row = conn.execute(
        "SELECT payload, state FROM action WHERE id = ?", (action_id,)
    ).fetchone()
    payload = json.loads(row["payload"])
    draft_id = payload.get("gmail_draft_id")
    if row["state"] == "executed" and draft_id and service:
        service.users().drafts().delete(userId="me", id=draft_id).execute()
    conn.execute("UPDATE action SET state = 'undone' WHERE id = ?", (action_id,))


def run(project_id=1):
    """Propose an action for every verdict and obligation that warrants one."""
    with db.connect() as conn:
        sow = conn.execute("SELECT sow_text FROM project WHERE id = ?",
                           (project_id,)).fetchone()["sow_text"]
        # scoped to this project: a second project's run must not wipe the
        # first one's proposals
        conn.execute(
            "DELETE FROM action WHERE (type = 'nudge' AND target_id IN"
            " (SELECT id FROM obligation WHERE project_id = ?))"
            " OR (type != 'nudge' AND target_id IN"
            " (SELECT m.id FROM message m JOIN thread t ON t.id = m.thread_id"
            "  WHERE t.project_id = ?))", (project_id, project_id))

        rows = conn.execute(
            "SELECT v.label, v.confidence, v.reasoning, m.id AS message_id,"
            " m.body, s.source_quote, s.item_text FROM verdict v"
            " JOIN message m ON m.id = v.message_id"
            " LEFT JOIN scope_item s ON s.id = v.scope_item_id"
            " JOIN thread t ON t.id = m.thread_id"
            " WHERE t.project_id = ? AND v.label = 'out_of_scope'"
            " ORDER BY m.received_at", (project_id,)
        ).fetchall()

        refused = 0
        for r in rows:
            if r["confidence"] == "unsure":
                # borderline asks are the developer's call, not the agent's
                flag(conn, r["message_id"], r["reasoning"])
                print(f"  flag    {r['body'][:56]}")
                continue
            try:
                d = change_order(dict(r), dict(r), sow)
            except UnsafeDraft as e:
                # a rail held. No draft is strictly better than an unsafe one.
                refused += 1
                print(f"  REFUSED {str(e)[:64]}")
                continue
            propose(conn, "draft_reply", r["message_id"], d)
            print(f"  reply   {r['body'][:56]}")
            print(f"          cites: {d.quoted_contract_text[:60]!r}")

        for o in conn.execute(
            "SELECT id, promise_text, due_at, created_at FROM obligation"
            " WHERE project_id = ? AND status = 'overdue' ORDER BY id",
            (project_id,)
        ).fetchall():
            try:
                d = nudge(dict(o), sow)
            except UnsafeDraft as e:
                refused += 1
                print(f"  REFUSED {str(e)[:64]}")
                continue
            propose(conn, "nudge", o["id"], d)
            print(f"  nudge   {o['promise_text'][:56]}")

        counts = dict(conn.execute(
            "SELECT type, COUNT(*) FROM action WHERE"
            " (type = 'nudge' AND target_id IN"
            "  (SELECT id FROM obligation WHERE project_id = ?))"
            " OR (type != 'nudge' AND target_id IN"
            "  (SELECT m.id FROM message m JOIN thread t ON t.id = m.thread_id"
            "   WHERE t.project_id = ?)) GROUP BY type",
            (project_id, project_id)).fetchall())
        print(f"\n{counts} — all 'proposed', nothing sent."
              f" {refused} draft(s) refused by the safety rails")


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
