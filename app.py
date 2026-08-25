"""The review surface.

The page is a thread with an annotation margin, not a dashboard. The contract
is quoted inline where it applies, because the claim being made — this request
is outside what you agreed — is only worth anything if the reader can see the
line it rests on.

Nothing here sends mail. Approving an action marks it approved; creating the
Gmail draft is a separate explicit step, and undo is always available.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

import db
import draft
import ledger

HERE = Path(__file__).parent
app = FastAPI()
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")

PROJECT_ID = 1


def day(iso, tz="UTC"):
    """Always render in the project owner's zone. A due date stored as
    end-of-day UTC reads as the next calendar day anywhere west of UTC."""
    return ledger.local_day(iso, tz)


def days_between(a, b):
    fmt = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (fmt(b) - fmt(a)).days


def with_placeholder(body, action):
    """Render the spot the agent refused to fill.

    The draft keeps [NEW DATE] verbatim for the whole life of the action — the
    agent never writes a date, and the audit trail should still show that after
    a human picks one. The chosen date lives beside the body, not inside it,
    and is substituted only for display.

    Escape first, then substitute Markup: Markup.replace() escapes a plain
    string replacement, so the control would render as visible text.
    """
    chosen = action["payload"].get("chosen_date")
    if chosen:
        pretty = datetime.fromisoformat(chosen).strftime("%-d %B %Y")
        control = Markup(
            f'<span class="placeholder is-set" title="You chose this date; '
            f'the agent left it blank.">{escape(pretty)}</span>')
    else:
        control = Markup(
            f'<input type="date" name="date" class="date-input" '
            f'aria-label="Choose the new delivery date" '
            f'hx-post="/action/{action["id"]}/date" hx-trigger="change" '
            f'hx-target="#action-{action["id"]}" hx-swap="outerHTML">')
    return Markup(escape(body).replace(draft.DATE_PLACEHOLDER, control))


templates.env.filters["day"] = day
templates.env.filters["with_placeholder"] = with_placeholder


def load(conn, project_id=PROJECT_ID):
    """Everything the page shows, in three queries."""
    project = conn.execute(
        "SELECT * FROM project WHERE id = ?", (project_id,)
    ).fetchone()
    if not project:
        return None

    messages = [dict(r) for r in conn.execute(
        "SELECT m.id, m.body, m.from_counterparty, m.received_at,"
        " v.label, v.confidence, v.reasoning, v.references_obligation_id,"
        " v.obligation_relation, v.accepts_change_to,"
        " s.source_quote, s.item_text, s.category, s.origin, s.agreed_at"
        " FROM message m"
        " JOIN thread t ON t.id = m.thread_id"
        " LEFT JOIN verdict v ON v.message_id = m.id"
        " LEFT JOIN scope_item s ON s.id = v.scope_item_id"
        " WHERE t.project_id = ? ORDER BY m.received_at", (project_id,))]

    obligations = [dict(r) for r in conn.execute(
        "SELECT id, message_id, promise_text, due_phrase, due_at, status,"
        " created_at, owed_by FROM obligation WHERE project_id = ?"
        " ORDER BY CASE status WHEN 'overdue' THEN 0 WHEN 'vague' THEN 1"
        " WHEN 'open' THEN 2 ELSE 3 END, due_at", (project_id,))]

    # actions carry no project column of their own: a nudge points at an
    # obligation, everything else at a message. Without this join the page
    # counts and the audit drawer quietly include every other project's work.
    actions = [dict(r) for r in conn.execute(
        "SELECT id, type, target_id, payload, state, created_at, executed_at"
        " FROM action WHERE (type = 'nudge' AND target_id IN"
        "  (SELECT id FROM obligation WHERE project_id = ?))"
        " OR (type != 'nudge' AND target_id IN"
        "  (SELECT m.id FROM message m JOIN thread t ON t.id = m.thread_id"
        "   WHERE t.project_id = ?)) ORDER BY id", (project_id, project_id))]
    for a in actions:
        a["payload"] = json.loads(a["payload"])

    today = messages[-1]["received_at"] if messages else None
    for o in obligations:
        o["late_by"] = (days_between(o["due_at"], today)
                        if o["due_at"] and o["status"] == "overdue" else None)

    by_target = {}
    for a in actions:
        if a["state"] != "undone":
            by_target.setdefault((a["type"], a["target_id"]), a)

    # a nudge belongs beside the message where the promise was made — that is
    # the moment the developer would want to see it, not a separate list
    nudges = {}
    for o in obligations:
        o["action"] = by_target.get(("nudge", o["id"]))
        if o["action"]:
            nudges.setdefault(o["message_id"], []).append((o, o["action"]))

    amendments = [dict(r) for r in conn.execute(
        "SELECT id, item_text, source_quote, origin_message_id, agreed_at"
        " FROM scope_item WHERE project_id = ? AND origin = 'amendment'"
        " ORDER BY agreed_at", (project_id,))]
    agreed_here = {a["origin_message_id"] for a in amendments}

    # quoting the same clause twice in a row spends the evidence for nothing.
    # The full text lands the first time; after that a back-reference is enough.
    seen_quote = None
    for m in messages:
        m["citation_is_repeat"] = (m["source_quote"] is not None
                                   and m["source_quote"] == seen_quote)
        if m["source_quote"]:
            seen_quote = m["source_quote"]

        # a message that agreed to a change needs no reply proposing one: it
        # is the answer, not the question. Keyed off the amendment itself,
        # which is the fact that survives a replay.
        own = None if m["id"] in agreed_here else (
            by_target.get(("draft_reply", m["id"]))
            or by_target.get(("flag", m["id"])))
        m["actions"] = ([own] if own else []) + [
            a for _, a in nudges.get(m["id"], [])]
        m["nudged"] = [o for o, _ in nudges.get(m["id"], [])]

    import classify

    return {
        "project": dict(project),
        "role": classify.ROLES[project["my_role"]],
        "amendments": amendments,
        "mine": [o for o in obligations if o["owed_by"] == "me"],
        "theirs": [o for o in obligations if o["owed_by"] == "them"],
        "projects": [dict(r) for r in conn.execute(
            "SELECT id, client_name FROM project ORDER BY id")],
        "messages": messages,
        "obligations": obligations,
        "actions": actions,
        "today": today,
        "counts": {
            "flagged": sum(1 for m in messages if m["label"] == "out_of_scope"),
            "overdue": sum(1 for o in obligations if o["status"] == "overdue"),
            "vague": sum(1 for o in obligations if o["status"] == "vague"),
            "they_owe": sum(1 for o in obligations
                            if o["owed_by"] == "them" and o["status"] == "overdue"),
            "waiting": sum(1 for a in actions if a["state"] == "proposed"),
            "clauses": conn.execute(
                "SELECT COUNT(*) FROM scope_item WHERE project_id = ?"
                " AND origin = 'contract'", (project_id,)).fetchone()[0],
            "amendments": len(amendments),
        },
    }


def spell(n):
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
             7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
             12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen",
             17: "seventeen", 18: "eighteen", 19: "nineteen", 20: "twenty",
             21: "twenty-one", 22: "twenty-two", 23: "twenty-three",
             24: "twenty-four", 25: "twenty-five"}
    return words.get(n, str(n))


@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    """The page quotes how much real material is behind the demo. Counting it
    rather than writing it down is the only way that claim stays true: it was
    already wrong once, because the threads grew and the sentence did not."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT p.client_name,"
            " (SELECT COUNT(*) FROM message m JOIN thread t ON t.id = m.thread_id"
            "  WHERE t.project_id = p.id) AS messages"
            " FROM project p WHERE p.my_role = 'contractor' ORDER BY p.id"
        ).fetchall()
    counts = [r["messages"] for r in rows]
    if counts:
        evidence = (f"{spell(len(counts))} real contracts, "
                    + " and ".join([", ".join(spell(c) for c in counts[:-1]),
                                    spell(counts[-1])]).strip(", ")
                    + " messages, nothing mocked")
    else:
        evidence = "run seed.py --replay to load the worked examples"
    return templates.TemplateResponse(request, "landing.html",
                                      {"evidence": evidence})


@app.get("/review", response_class=HTMLResponse)
def index(request: Request, project: int = PROJECT_ID):
    with db.connect() as conn:
        data = load(conn, project)
    if not data:
        return HTMLResponse(
            "<p style='font:16px system-ui;padding:3rem'>No project loaded. "
            "Run <code>python seed.py --replay</code>.</p>")
    return templates.TemplateResponse(request, "index.html", data)


def _one_action(conn, action_id):
    row = conn.execute(
        "SELECT id, type, target_id, payload, state FROM action WHERE id = ?",
        (action_id,)).fetchone()
    a = dict(row)
    a["payload"] = json.loads(a["payload"])
    return a


@app.post("/action/{action_id}/approve", response_class=HTMLResponse)
def approve(request: Request, action_id: int):
    with db.connect() as conn:
        draft.approve(conn, action_id)
        action = _one_action(conn, action_id)
    return templates.TemplateResponse(request, "_action.html", {"a": action})


@app.post("/action/{action_id}/date", response_class=HTMLResponse)
def set_date(request: Request, action_id: int, date: str = Form("")):
    """The one thing the agent would not decide. Stored beside the draft so the
    body it wrote stays intact in the record."""
    with db.connect() as conn:
        draft.set_date(conn, action_id, date)
        action = _one_action(conn, action_id)
    return templates.TemplateResponse(request, "_action.html", {"a": action})


@app.post("/action/{action_id}/undo", response_class=HTMLResponse)
def undo(request: Request, action_id: int):
    with db.connect() as conn:
        # no Gmail service passed: nothing was pushed, so nothing to delete
        draft.undo(conn, action_id)
        action = _one_action(conn, action_id)
    return templates.TemplateResponse(request, "_action.html", {"a": action})


@app.get("/audit", response_class=HTMLResponse)
def audit(request: Request, project: int = PROJECT_ID):
    with db.connect() as conn:
        data = load(conn, project)
    return templates.TemplateResponse(request, "_audit.html", data)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8000)
