"""Message classifier — one call per inbound message.

Cache shape matters more than cache volume. Prompt caching is a prefix match,
so the layout is:

    system: instructions + scope items   <- stable for the whole project
    ^ cache_control breakpoint
    messages: thread context + the one message being classified   <- volatile

Putting the growing thread above the breakpoint would invalidate the cache on
every message and quietly pay full input price on all of them.

`label` answers one question only: how does this message relate to the
contract. Whether the message also contains a promise is a separate axis — an
out-of-scope request can be answered with a commitment, which is the worst
case and needs both recorded. So promise_text/due_phrase are independent of
label, not a fifth label value.
"""
import json
import sys

import anthropic
from pydantic import BaseModel

import db

MODEL = "claude-opus-5"

# below this, flag for human review instead of acting. The borderline asks
# ("could the logo animate?") are supposed to land here.
ESCALATE_BELOW = 0.7

SYSTEM = """You classify messages in an email thread between a freelance \
developer and their client, against the contract the two of them signed.

The contract's scope items are listed below. They are the ONLY definition of \
what is in scope. Your own sense of what a website project usually includes is \
irrelevant and must not influence the verdict.

<scope_items>
{scope_items}
</scope_items>

For the message you are given, return:

label — how the message relates to the contract:
  out_of_scope   the message asks for work not covered by the scope items, or
                 work an exclusion rules out. Cite the scope item.
  in_scope       the message concerns work the scope items already cover.
  new_commitment the message contains a promise but raises no scope question.
  noise          scheduling, pleasantries, logistics, acknowledgements.

scope_item_id — the id of the scope item that decides the verdict. Required for
out_of_scope and in_scope. Null for noise.

reasoning — one sentence, addressed to the developer. For out_of_scope, say
which scope item is contradicted and how.

confidence — 0 to 1. Be honest. A small ambiguous request that could plausibly
sit inside an existing deliverable should score low, not be forced into a
confident verdict. Low confidence sends it to a human, which is the correct
outcome for a genuinely borderline ask.

promise_text — if the message contains a promise to do something, the promise
in the writer's own words. Null otherwise. This is INDEPENDENT of label: a
message can be out_of_scope and contain a promise at the same time, and that
combination matters most of all.

due_phrase — the words that state when, copied verbatim ("by Friday", "end of
this week", "soon", "tomorrow"). Null if the promise names no time. Do not
convert it to a date and do not invent one; copy what was written."""


class Verdict(BaseModel):
    label: str
    scope_item_id: int | None
    reasoning: str
    confidence: float
    promise_text: str | None
    due_phrase: str | None


LABELS = {"in_scope", "out_of_scope", "new_commitment", "noise"}


def render_scope(items):
    """Stable rendering — ordered by id, so the cached prefix stays byte-identical."""
    return "\n".join(
        f"[{i['id']}] ({i['category']}) {i['item_text']}\n"
        f"     contract text: \"{i['source_quote']}\""
        for i in sorted(items, key=lambda i: i["id"])
    )


def build_system(items):
    """System block carrying the cache breakpoint."""
    return [
        {
            "type": "text",
            "text": SYSTEM.format(scope_items=render_scope(items)),
            "cache_control": {"type": "ephemeral"},
        }
    ]


def build_messages(history, target):
    """Thread context plus the one message under judgement — all volatile, all
    after the breakpoint."""
    context = "\n\n".join(
        f"{'CLIENT' if m['from_client'] else 'DEVELOPER'} ({m['received_at']}):\n"
        f"{m['body']}"
        for m in history
    )
    return [
        {
            "role": "user",
            "content": f"<thread_so_far>\n{context or '(none)'}\n</thread_so_far>\n\n"
            f"<message_to_classify from=\""
            f"{'client' if target['from_client'] else 'developer'}\" "
            f"sent=\"{target['received_at']}\">\n{target['body']}\n"
            "</message_to_classify>\n\nClassify this message.",
        }
    ]


def validate(verdict, items):
    """Reject a verdict that points at a scope item that does not exist. Same
    reason ingest raises on a bad quote: a dangling id renders as a citation
    with nothing behind it."""
    if verdict.label not in LABELS:
        raise ValueError(f"unknown label {verdict.label!r}")
    if verdict.scope_item_id is not None:
        if verdict.scope_item_id not in {i["id"] for i in items}:
            raise ValueError(
                f"scope_item_id {verdict.scope_item_id} is not in this project"
            )
    elif verdict.label in ("in_scope", "out_of_scope"):
        raise ValueError(f"{verdict.label} verdict with no scope_item_id")
    return verdict


def classify(target, history, items, client=None):
    client = client or anthropic.Anthropic()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=build_system(items),
        thinking={"type": "adaptive"},
        messages=build_messages(history, target),
        output_format=Verdict,
    )
    return validate(response.parsed_output, items), response


def store(conn, message_id, verdict):
    conn.execute(
        "INSERT OR REPLACE INTO verdict (message_id, label, scope_item_id,"
        " reasoning, confidence) VALUES (?,?,?,?,?)",
        (
            message_id,
            verdict.label,
            verdict.scope_item_id,
            verdict.reasoning,
            verdict.confidence,
        ),
    )


def run(project_id=1):
    """Walk the thread in order. Chronological order is required: the classifier
    sees only the messages that preceded the one it is judging, same as the
    developer did."""
    with db.connect() as conn:
        items = [
            dict(r)
            for r in conn.execute(
                "SELECT id, item_text, source_quote, category FROM scope_item"
                " WHERE project_id = ? ORDER BY id",
                (project_id,),
            )
        ]
        if not items:
            sys.exit("no scope items — run ingest.py first")

        messages = [
            dict(r)
            for r in conn.execute(
                "SELECT m.* FROM message m JOIN thread t ON t.id = m.thread_id"
                " WHERE t.project_id = ? ORDER BY m.received_at",
                (project_id,),
            )
        ]

        cached = 0
        for n, target in enumerate(messages):
            verdict, response = classify(target, messages[:n], items)
            store(conn, target["id"], verdict)
            cached += response.usage.cache_read_input_tokens

            mark = "!" if verdict.confidence < ESCALATE_BELOW else " "
            print(f"{mark} {verdict.label:15} {verdict.confidence:.2f} "
                  f"{target['body'][:52]}")
            if verdict.promise_text:
                print(f"    promise: {verdict.promise_text!r}"
                      f" due={verdict.due_phrase!r}")

        # if this is 0 across a whole thread, the prefix is being invalidated
        print(f"\ncache reads: {cached} tokens across {len(messages)} calls")


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
