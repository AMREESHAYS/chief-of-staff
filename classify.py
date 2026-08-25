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
from types import SimpleNamespace

from pydantic import BaseModel

import amend
import db
import ledger
import llm

# this band means stop and ask a human rather than act
ESCALATE = "unsure"

# Which side of the contract the user is on. The engine is the same either
# way: whether a request is in scope is a property of the request, not of who
# is reading. Only whose promise it is depends on the side.
ROLES = {
    "contractor": {"me": "the contractor", "them": "the client",
                   "them_short": "client"},
    "buyer": {"me": "the buyer", "them": "the supplier",
              "them_short": "supplier"},
}

SYSTEM = """You classify messages in an email thread between {me} and {them}, \
against the contract the two of them signed. You are working for {me}.

The contract's scope items are listed below. They are the ONLY definition of \
what is in scope. Your own sense of what a website project usually includes is \
irrelevant and must not influence the verdict.

<scope_items>
{scope_items}
</scope_items>

The message may also arrive with a list of commitments the developer has already made and not yet closed. Those appear in the message itself, not here.

For the message you are given, return:

label — how the message relates to the contract:
  out_of_scope   the message asks for work not covered by the scope items, or
                 work an exclusion rules out. Cite the scope item.
  in_scope       the message concerns work the scope items already cover.
  new_commitment the message contains a promise but raises no scope question.
  noise          scheduling, pleasantries, logistics, acknowledgements.

scope_item_id — the id of the scope item that decides the verdict. Required for
out_of_scope and in_scope. Null for noise.

reasoning — one sentence, addressed to {me}, explaining the verdict.
Never mention scope item numbers or ids. They are internal database keys, they
mean nothing to the person reading this, and the interface already shows the
contract line itself directly above your sentence. Refer to what the clause
says, not to its number: "the agreement excludes e-commerce entirely", never
"contradicts scope item 9".

confidence — exactly one of: certain, likely, unsure.
  certain  the message plainly matches or plainly contradicts a scope item,
           with wording that leaves no reasonable second reading.
  likely   the right scope item is clear, but the message is loosely worded.
  unsure   ANY of the following is true, and none of them are judgement calls:
             - the request could reasonably belong to two or more scope items
             - it is a small addition that might sit inside an existing
               deliverable or might not, and the contract does not say
             - no scope item shares clear subject matter with the request
             - the ONLY clause that covers it is a general one about work not
               described in the agreement, or about changes requiring written
               agreement. A catch-all clause is not evidence that a specific
               request is out of scope; it is what you fall back on when no
               specific clause speaks to it, which is precisely the case a
               human should decide. Certainty requires a clause about the
               subject matter of the request itself.
           When one of those holds, return unsure. Do not resolve the
           ambiguity yourself — unsure routes it to the developer, which is
           the correct outcome and not a failure.

promise_text — if the message contains a promise to do something, the promise
in the writer's own words. Null otherwise. Either party can make one; record
it whoever it came from. This is INDEPENDENT of label: a
message can be out_of_scope and contain a promise at the same time, and that
combination matters most of all.

due_phrase — the words that state when, copied verbatim ("by Friday", "end of
this week", "soon", "tomorrow"). Null if the promise names no time. Do not
convert it to a date and do not invent one; copy what was written.

references_obligation_id — if the message chases, asks about, or fulfils one
of the open commitments listed with it, that commitment's id. Null otherwise.
This is INDEPENDENT of label too: a client writing "any luck with that?" is
conversationally noise and is also the client chasing a specific overdue
promise. Record both.

accepts_change_to — if this message agrees to one of the listed out-of-scope
requests, that request's id. Null otherwise, which is the usual answer.

Agreement means a decision, not warmth. "Go ahead", "approved", "yes let's do
it at that price" are agreement. "Sounds good, let me think", "how much would
that be", "I'll check with my partner" are not. When in doubt it is not
agreement — a wrong yes here silently widens what someone is owed.

obligation_relation — required whenever references_obligation_id is set, null
otherwise. Exactly one of:
  chases   the message asks about, follows up on, or complains about the
           commitment. It is still outstanding.
  fulfils  the message delivers the thing that was promised. Only the party
           who made the promise can close it: handing the work over counts,
           the other side saying "thanks, got it" does not.
Getting this wrong in the chases direction leaves delivered work sitting in
the overdue list, so read the message for delivery, not for intent."""


class Verdict(BaseModel):
    label: str
    accepts_change_to: int | None = None
    scope_item_id: int | None
    reasoning: str
    confidence: str
    promise_text: str | None
    due_phrase: str | None
    references_obligation_id: int | None
    obligation_relation: str | None


LABELS = {"in_scope", "out_of_scope", "new_commitment", "noise"}
BANDS = {"certain", "likely", "unsure"}
RELATIONS = {"chases", "fulfils"}


def render_scope(items):
    """Stable rendering — ordered by id, so the cached prefix stays byte-identical."""
    return "\n".join(
        f"[{i['id']}] ({i['category']}) {i['item_text']}\n"
        + (f"     agreed by email {i['agreed_at'][:10]}, their words:"
           f" \"{i['source_quote']}\""
           if i.get("origin") == "amendment"
           else f"     contract text: \"{i['source_quote']}\"")
        for i in sorted(items, key=lambda i: i["id"])
    )


def build_system(items, role="contractor"):
    """The cacheable half. Byte-identical for every message in a project —
    llm.parse attaches the provider's cache breakpoint to it."""
    return SYSTEM.format(scope_items=render_scope(items), **ROLES[role])


def build_messages(history, target, obligations=(), tz="UTC", role="contractor",
                   change_requests=()):
    """Thread context, open commitments, and the one message under judgement —
    all volatile, all after the breakpoint."""
    them = ROLES[role]["them_short"].upper()
    context = "\n\n".join(
        f"{them if m['from_counterparty'] else 'YOU'} ({m['received_at']}):\n"
        f"{m['body']}"
        for m in history
    )
    return [
        {
            "role": "user",
            "text": f"<thread_so_far>\n{context or '(none)'}\n</thread_so_far>\n\n"
            f"<open_commitments>\n{ledger.render(obligations, tz)}\n"
            "</open_commitments>\n\n"
            f"<awaiting_your_answer>\n{amend.render(change_requests)}\n"
            "</awaiting_your_answer>\n\n"
            f"<message_to_classify from=\""
            f"{ROLES[role]['them_short'] if target['from_counterparty'] else 'you'}\" "
            f"sent=\"{target['received_at']}\">\n{target['body']}\n"
            "</message_to_classify>\n\nClassify this message.",
        }
    ]


def validate(verdict, items, obligations=(), change_requests=(), sender=None):
    """Reject a verdict that points at something that does not exist. Same
    reason ingest raises on a bad quote: a dangling id renders as a citation
    with nothing behind it."""
    if verdict.label not in LABELS:
        raise ValueError(f"unknown label {verdict.label!r}")
    if verdict.confidence not in BANDS:
        raise ValueError(f"unknown confidence band {verdict.confidence!r}")
    if verdict.accepts_change_to is not None:
        if verdict.accepts_change_to not in {r["id"] for r in change_requests}:
            raise ValueError(
                f"accepts_change_to {verdict.accepts_change_to} is not a"
                " request that was awaiting an answer")
    if verdict.references_obligation_id is not None:
        if verdict.references_obligation_id not in {o["id"] for o in obligations}:
            raise ValueError(
                f"references_obligation_id {verdict.references_obligation_id}"
                " was not among the open commitments"
            )
        if verdict.obligation_relation not in RELATIONS:
            owed = next((o.get("owed_by") for o in obligations
                         if o["id"] == verdict.references_obligation_id), None)
            if owed and sender and owed != sender:
                # you cannot deliver someone else's promise, so this is a chase
                verdict.obligation_relation = "chases"
            else:
                raise ValueError(
                    f"reference to obligation {verdict.references_obligation_id}"
                    f" with unusable relation {verdict.obligation_relation!r}"
                )
    elif verdict.obligation_relation is not None:
        raise ValueError(
            f"obligation_relation {verdict.obligation_relation!r} with nothing"
            " to relate to"
        )
    if verdict.scope_item_id is not None:
        if verdict.scope_item_id not in {i["id"] for i in items}:
            raise ValueError(
                f"scope_item_id {verdict.scope_item_id} is not in this project"
            )
    elif verdict.label in ("in_scope", "out_of_scope"):
        raise ValueError(f"{verdict.label} verdict with no scope_item_id")
    return verdict


def classify(target, history, items, obligations=(), parse_fn=None, tz="UTC",
             role="contractor", change_requests=(), retries=1):
    """One verdict, validated. A rejected verdict is asked again with the fault
    named — the same treatment a bad quote gets at ingest, rather than losing
    the message from the thread entirely."""
    parse_fn = parse_fn or llm.parse
    system = build_system(items, role)
    turns = build_messages(history, target, obligations, tz, role, change_requests)
    sender = "them" if target["from_counterparty"] else "me"

    for attempt in range(retries + 1):
        result = parse_fn(system, turns, Verdict)
        try:
            verdict = validate(result.parsed, items, obligations,
                               change_requests, sender)
        except ValueError as e:
            if attempt == retries:
                raise
            turns = turns + [
                {"role": "assistant", "text": result.parsed.model_dump_json()},
                {"role": "user",
                 "text": f"That verdict was rejected: {e}\n\nClassify the same "
                         "message again, fixing exactly that."},
            ]
        else:
            return verdict, result


def store(conn, message_id, verdict):
    conn.execute(
        "INSERT OR REPLACE INTO verdict (message_id, label, scope_item_id,"
        " reasoning, confidence, references_obligation_id, obligation_relation,"
        " accepts_change_to) VALUES (?,?,?,?,?,?,?,?)",
        (
            message_id,
            verdict.label,
            verdict.scope_item_id,
            verdict.reasoning,
            verdict.confidence,
            verdict.references_obligation_id,
            verdict.obligation_relation,
            verdict.accepts_change_to,
        ),
    )


def run(project_id=1):
    """Walk the thread in order, writing obligations as they are made.

    Chronological order is required twice over: the classifier sees only the
    messages that preceded the one it is judging, and it can only reference
    commitments that had already been made when that message arrived.
    """
    with db.connect() as conn:
        project = conn.execute(
            "SELECT owner_tz, my_role FROM project WHERE id = ?", (project_id,)
        ).fetchone()
        items = [
            dict(r)
            for r in conn.execute(
                "SELECT id, item_text, source_quote, category, origin,"
                " agreed_at FROM scope_item WHERE project_id = ?"
                " AND origin = 'contract' ORDER BY id",
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

        # order matters: verdict holds a foreign key into obligation, and a
        # nudge points at one by id. Clearing obligations first fails outright;
        # leaving the actions behind would leave them pointing at ids that are
        # about to be reissued.
        conn.execute(
            "DELETE FROM action WHERE (type = 'nudge' AND target_id IN"
            "  (SELECT id FROM obligation WHERE project_id = ?))"
            " OR (type != 'nudge' AND target_id IN"
            "  (SELECT m.id FROM message m JOIN thread t ON t.id = m.thread_id"
            "   WHERE t.project_id = ?))", (project_id, project_id))
        conn.execute(
            "DELETE FROM verdict WHERE message_id IN"
            " (SELECT m.id FROM message m JOIN thread t ON t.id = m.thread_id"
            "  WHERE t.project_id = ?)", (project_id,))
        conn.execute("DELETE FROM obligation WHERE project_id = ?", (project_id,))
        # amendments are derived from this run, so they go with it
        conn.execute("DELETE FROM scope_item WHERE project_id = ?"
                     " AND origin = 'amendment'", (project_id,))
        cached, rejected = 0, 0
        for n, target in enumerate(messages):
            open_now = ledger.open_obligations(conn, project_id,
                                               target["received_at"])
            pending = amend.open_change_requests(conn, project_id,
                                                 target["received_at"])
            try:
                verdict, result = classify(target, messages[:n], items, open_now,
                                           tz=project["owner_tz"],
                                           role=project["my_role"],
                                           change_requests=pending)
            except ValueError as e:
                # the verdict was malformed and validate() refused it. One bad
                # message must not abandon the rest of the thread.
                # ponytail: no retry — add one if a real provider starts
                # failing here, Gemini has not
                rejected += 1
                print(f"x {'rejected':15} {str(e)[:38]:8} {target['body'][:46]}")
                continue
            # the wide verdict schema answers this unreliably, so when it
            # could matter the question is asked on its own
            if (pending and not verdict.accepts_change_to
                    and amend.widens_scope(SimpleNamespace(accepts_change_to=1),
                                           target, project["my_role"])):
                verdict.accepts_change_to = amend.detect_acceptance(
                    target["body"], pending)

            store(conn, target["id"], verdict)
            ledger.record(conn, project_id, target, verdict, project["owner_tz"])
            # only the party who made a promise can deliver it. Before both
            # sides were tracked this read "not from_counterparty", which
            # silently means the counterparty can never close their own.
            if verdict.obligation_relation == "fulfils":
                owed = conn.execute(
                    "SELECT owed_by FROM obligation WHERE id = ?",
                    (verdict.references_obligation_id,)).fetchone()
                sender = "them" if target["from_counterparty"] else "me"
                if owed and owed["owed_by"] == sender:
                    ledger.close(conn, verdict.references_obligation_id)
            cached += result.cache_read_tokens

            if amend.widens_scope(verdict, target, project["my_role"]):
                request = next(r for r in pending
                               if r["id"] == verdict.accepts_change_to)
                try:
                    a = amend.extract(request["body"], target["body"])
                except amend.NotAgreed as e:
                    print(f"x {'no amendment':15} {str(e)[:44]}")
                else:
                    amend.store(conn, project_id, a, target)
                    items = [dict(r) for r in conn.execute(
                        "SELECT id, item_text, source_quote, category, origin,"
                        " agreed_at FROM scope_item WHERE project_id = ?"
                        " ORDER BY id", (project_id,))]
                    print(f"+ {'AMENDED':15} scope now covers: {a.item_text[:44]}")

            mark = "!" if verdict.confidence == ESCALATE else " "
            print(f"{mark} {verdict.label:15} {verdict.confidence:8} "
                  f"{target['body'][:46]}")
            if verdict.promise_text and not target["from_counterparty"]:
                print(f"    promise: {verdict.promise_text[:60]!r}"
                      f" due={verdict.due_phrase!r}")
            if verdict.references_obligation_id:
                print(f"    -> {verdict.obligation_relation} obligation "
                      f"#{verdict.references_obligation_id}")

        # the thread's own end date, not today — the demo replays history
        late = ledger.sweep(conn, project_id, messages[-1]["received_at"])
        print(f"\n{result.provider}/{result.model} — {late} overdue, "
              f"{rejected} verdict(s) rejected, cache reads: {cached} tokens")

        for o in ledger.open_obligations(conn, project_id, "9999"):
            print(f"  [{o['status']:7}] {o['promise_text'][:58]!r}"
                  f" due={ledger._due_label(o, project['owner_tz'])}")


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
