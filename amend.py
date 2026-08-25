"""Amendments — the contract stops being frozen at signature.

A signed document is the starting position, not the final one. Scope changes
by email: someone asks for something outside the agreement, the other side
sends a change order, and a reply says yes. From that moment the work IS in
scope, and a system that keeps citing the original contract is now wrong.

Two things make this safe rather than a licence to invent scope:

1. **Only the paying side can widen the scope.** The contractor proposing a
   change order does not create one; agreement does. Enforced in code, since a
   model asked "did they agree?" will find agreement in enthusiasm.

2. **The citation is a span of the accepting message**, copied verbatim, and
   checked against that message the same way clause quotes are checked against
   the contract. An amendment nobody actually wrote is exactly the fabrication
   the rest of this system refuses.

The result reads back as provenance: this is in scope under the contract, or
in scope because you agreed to it on the 26th — and here are their words.
"""
import sys

from pydantic import BaseModel

import db
import llm
from ingest import _norm

SYSTEM = """A client has agreed in writing to work that the signed contract \
did not cover. Turn their agreement into a scope item, the same shape as a \
clause of the contract itself.

You are given the original request, the change order that answered it, and the \
message in which the client accepted.

item_text — what the contractor now owes, in one plain sentence. Describe only \
what was actually agreed. If the acceptance narrowed the request, follow the \
acceptance, not the request.

source_quote — the words in the ACCEPTING MESSAGE that constitute agreement, \
copied character for character. Not from the request, not from the change \
order, and not a summary. This is the evidence that the scope changed, so it \
must be checkable against what they actually wrote.

category — always 'amendment'.

conditions — anything the acceptance made the agreement depend on: a sequence, \
a deadline, a limit. Null if unconditional. Do not invent a condition that was \
not stated."""


class Amendment(BaseModel):
    item_text: str
    source_quote: str      # verbatim span of the accepting message
    conditions: str | None


class NotAgreed(Exception):
    """The quote said to be agreement is not in the message that supposedly
    gave it. Loud, for the same reason a fabricated clause quote is."""


def validate(amendment, accepting_message_body):
    if _norm(amendment.source_quote) not in _norm(accepting_message_body):
        raise NotAgreed(
            "the words cited as agreement are not in the accepting message: "
            f"{amendment.source_quote!r}"
        )
    return amendment


def extract(request_body, accepting_body, change_order_body="(none sent)",
            parse_fn=None, retries=1):
    parse_fn = parse_fn or llm.parse
    turns = [{
        "role": "user",
        "text": f"<original_request>\n{request_body}\n</original_request>\n\n"
                f"<change_order_sent>\n{change_order_body}\n</change_order_sent>"
                f"\n\n<their_acceptance>\n{accepting_body}\n</their_acceptance>"
                "\n\nWrite the scope item this agreement creates.",
    }]
    for attempt in range(retries + 1):
        result = parse_fn(SYSTEM, turns, Amendment)
        try:
            return validate(result.parsed, accepting_body)
        except NotAgreed as e:
            if attempt == retries:
                raise
            turns = turns + [
                {"role": "assistant", "text": result.parsed.model_dump_json()},
                {"role": "user",
                 "text": f"Rejected: {e}\n\nCopy source_quote character for "
                         "character from <their_acceptance>."},
            ]


def widens_scope(verdict, message, my_role):
    """Does this message actually enlarge what is owed?

    Only the paying side can. A contractor writing "great, I'll start Monday"
    is enthusiasm; a model asked "did they agree?" will read agreement into it,
    and the cost of a wrong yes is that someone is quietly owed more work.

    Whose side is "paying" depends on which end of the contract we are reading
    from, which is why role is a parameter and not an assumption.
    """
    if verdict.accepts_change_to is None:
        return False
    counterparty_pays = my_role == "contractor"
    return bool(message["from_counterparty"]) is counterparty_pays


def store(conn, project_id, amendment, accepting_message):
    """An amendment joins the scope items and is cited like any other."""
    text = amendment.item_text
    if amendment.conditions:
        text = f"{text} ({amendment.conditions})"
    return conn.execute(
        "INSERT INTO scope_item (project_id, item_text, source_quote, category,"
        " origin, origin_message_id, agreed_at) VALUES (?,?,?,?,?,?,?)",
        (project_id, text, amendment.source_quote, "amendment", "amendment",
         accepting_message["id"], accepting_message["received_at"]),
    ).lastrowid


def open_change_requests(conn, project_id, as_of):
    """Out-of-scope asks already on the table that nobody has accepted yet.

    Deliberately not conditioned on a draft reply existing: classification runs
    before drafting, and a client can agree to a request whether or not this
    system offered to answer it.
    """
    return [
        dict(r)
        for r in conn.execute(
            "SELECT m.id, m.body, m.received_at FROM message m"
            " JOIN thread t ON t.id = m.thread_id"
            " JOIN verdict v ON v.message_id = m.id"
            " WHERE t.project_id = ? AND v.label = 'out_of_scope'"
            " AND m.received_at < ?"
            " AND NOT EXISTS (SELECT 1 FROM scope_item s"
            "                 WHERE s.project_id = t.project_id"
            "                   AND s.origin = 'amendment'"
            "                   AND s.agreed_at > m.received_at)"
            " ORDER BY m.received_at", (project_id, as_of))
    ]


def render(requests):
    if not requests:
        return "(none)"
    return "\n".join(
        f"[{r['id']}] asked {r['received_at'][:10]}: \"{r['body'][:150]}\""
        for r in requests
    )


def main(project_id=1):
    """Report the amendments already recorded for a project."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT s.item_text, s.source_quote, s.agreed_at, m.body"
            " FROM scope_item s LEFT JOIN message m ON m.id = s.origin_message_id"
            " WHERE s.project_id = ? AND s.origin = 'amendment'"
            " ORDER BY s.agreed_at", (project_id,)).fetchall()
    if not rows:
        print("no amendments — the contract stands as signed")
        return
    for r in rows:
        print(f"agreed {r['agreed_at'][:10]}: {r['item_text']}")
        print(f"   their words: \"{r['source_quote']}\"")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
