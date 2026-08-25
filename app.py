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

from fastapi import FastAPI, Request
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


def with_placeholder(body):
    """Mark the spot the developer must fill in.

    Escape first, then substitute Markup — Markup.replace() escapes a plain
    string replacement, which renders the span as visible text instead of
    applying it.
    """
    return Markup(escape(body).replace(
        draft.DATE_PLACEHOLDER,
        Markup('<span class="placeholder">[you choose the date]</span>'),
    ))


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
        " v.obligation_relation, s.source_quote, s.item_text, s.category"
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

    # quoting the same clause twice in a row spends the evidence for nothing.
    # The full text lands the first time; after that a back-reference is enough.
    seen_quote = None
    for m in messages:
        m["citation_is_repeat"] = (m["source_quote"] is not None
                                   and m["source_quote"] == seen_quote)
        if m["source_quote"]:
            seen_quote = m["source_quote"]

        own = by_target.get(("draft_reply", m["id"])) or by_target.get(
            ("flag", m["id"]))
        m["actions"] = ([own] if own else []) + [
            a for _, a in nudges.get(m["id"], [])]
        m["nudged"] = [o for o, _ in nudges.get(m["id"], [])]

    import classify

    return {
        "project": dict(project),
        "role": classify.ROLES[project["my_role"]],
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
                "SELECT COUNT(*) FROM scope_item WHERE project_id = ?",
                (project_id,)).fetchone()[0],
        },
    }


@app.get("/", response_class=HTMLResponse)
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
