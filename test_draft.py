"""Checks for the draft safety rails.

Everything else in this project fails visibly. These failures would look
completely normal: a fluent, confident email that quotes a contract line that
isn't there, invents a price, or commits the developer to a date they never
agreed to — and then gets sent.

Run: .venv/bin/python test_draft.py     (no API calls, no framework)
"""
import json
from pathlib import Path
from types import SimpleNamespace

import draft
from draft import DATE_PLACEHOLDER, Draft, UnsafeDraft

SOW = """The contractor will build a five-page static marketing website.

Online payments, shopping cart, and any e-commerce functionality are
excluded from this agreement.

Fee: INR 85,000, fixed. 40% on signing, 60% on delivery."""

QUOTE = "any e-commerce functionality are excluded from this agreement"


def draft_of(body, quote=QUOTE):
    return Draft(body=body, quoted_contract_text=quote)


# --- the citation must be real, and must be visible to the client --------

def test_a_faithful_change_order_passes():
    body = (f"Happy to look at this. The agreement we signed says "
            f"\"{QUOTE}\", so it sits outside the current scope. I can put "
            "together a change order with a separate price if you'd like.")
    assert draft.validate_change_order(draft_of(body), SOW).body == body


def test_a_quote_not_in_the_contract_is_refused():
    fake = "e-commerce is not part of this project"
    body = f'Our agreement says "{fake}", so this is out of scope.'
    try:
        draft.validate_change_order(draft_of(body, fake), SOW)
    except UnsafeDraft as e:
        assert "not in the contract" in str(e)
    else:
        raise AssertionError("a fabricated contract quote reached the client")


def test_a_citation_missing_from_the_body_is_refused():
    # cited in the metadata, absent from the email the client actually reads
    body = "This is out of scope under our agreement. Happy to quote for it."
    try:
        draft.validate_change_order(draft_of(body), SOW)
    except UnsafeDraft as e:
        assert "client would read" in str(e)
    else:
        raise AssertionError("invisible citation accepted")


def test_an_uncited_change_order_is_refused():
    try:
        draft.validate_change_order(draft_of("Out of scope, sorry.", None), SOW)
    except UnsafeDraft as e:
        assert "cites no contract text" in str(e)
    else:
        raise AssertionError("uncited change order accepted")


def test_rewrapped_citation_still_passes():
    # the model reflowed the line break; no words changed
    wrapped = "any e-commerce functionality are excluded\nfrom this agreement"
    body = f'The contract says "{wrapped}" — outside scope.'
    assert draft.validate_change_order(draft_of(body, wrapped), SOW)


# --- money is never invented ---------------------------------------------

def test_an_invented_price_is_refused():
    body = (f'The contract says "{QUOTE}". I can add it for INR 25,000.')
    try:
        draft.validate_change_order(draft_of(body), SOW)
    except UnsafeDraft as e:
        assert "invents figures" in str(e)
    else:
        raise AssertionError("the agent quoted a price it made up")


def test_an_invented_discount_is_refused():
    body = f'The contract says "{QUOTE}". I could do it at 20% off.'
    try:
        draft.validate_change_order(draft_of(body), SOW)
    except UnsafeDraft as e:
        assert "invents figures" in str(e)
    else:
        raise AssertionError("the agent invented a discount")


def test_a_figure_from_the_contract_is_allowed():
    # repeating the agreed fee is fine; inventing a new one is not
    body = (f'The contract says "{QUOTE}". The agreed fee of INR 85,000 covers '
            "the original five pages only.")
    assert draft.validate_change_order(draft_of(body), SOW)


def test_figure_matching_ignores_spacing():
    assert draft.invented_figures("costs INR85,000 total", SOW) == []


# --- dates are the developer's to give -----------------------------------

def test_a_nudge_without_the_placeholder_is_refused():
    body = ("The Collections page was due on 14 August and I haven't sent it. "
            "I'll have it over to you shortly.")
    try:
        draft.validate_nudge(draft_of(body, None), SOW)
    except UnsafeDraft as e:
        assert DATE_PLACEHOLDER in str(e)
    else:
        raise AssertionError("nudge shipped with no date placeholder")


def test_a_nudge_that_invents_a_date_is_refused():
    # the failure this rail exists for: a specific promise nobody made
    body = ("The Collections page was due 14 August. I'll have it to you by "
            "Wednesday.")
    try:
        draft.validate_nudge(draft_of(body, None), SOW)
    except UnsafeDraft:
        pass
    else:
        raise AssertionError("the agent committed the developer to a date")


def test_a_correct_nudge_passes():
    body = (f"The Collections page was due on 14 August and I haven't got it "
            f"to you. I'll have it over by {DATE_PLACEHOLDER}. Sorry for the "
            "silence.")
    assert draft.validate_nudge(draft_of(body, None), SOW).body == body


def test_a_nudge_may_not_quote_money_either():
    body = f"Late on the Collections page. Due {DATE_PLACEHOLDER}. Refund 10%?"
    try:
        draft.validate_nudge(draft_of(body, None), SOW)
    except UnsafeDraft as e:
        assert "invents figures" in str(e)
    else:
        raise AssertionError("nudge invented a refund")


def test_chasing_and_apologising_are_different_letters():
    """Late work reads one way when it is yours and another when it is
    theirs. Sending a supplier an apology for their own delay is worse than
    sending nothing."""
    assert "apologise" in draft.CHASE_SYSTEM.lower()
    assert "not the sender" in draft.CHASE_SYSTEM
    assert DATE_PLACEHOLDER in draft.CHASE_SYSTEM
    assert DATE_PLACEHOLDER in draft.OWN_NUDGE_SYSTEM
    # neither side may be committed to a date by the agent
    for prompt in (draft.CHASE_SYSTEM, draft.OWN_NUDGE_SYSTEM):
        assert "fee" in prompt or "price" in prompt


def test_nudge_picks_the_prompt_from_who_owes():
    seen = []

    def fake(system, turns, schema):
        seen.append(system)
        return SimpleNamespace(parsed=Draft(
            body=f"Late. New date: {DATE_PLACEHOLDER}.",
            quoted_contract_text=None))

    base = {"promise_text": "the staging link", "created_at": "2026-08-05",
            "due_at": "2026-08-06"}
    draft.nudge({**base, "owed_by": "them"}, SOW, parse_fn=fake)
    draft.nudge({**base, "owed_by": "me"}, SOW, parse_fn=fake)
    assert seen[0] is draft.CHASE_SYSTEM
    assert seen[1] is draft.OWN_NUDGE_SYSTEM


# --- nothing is ever sent ------------------------------------------------

def test_the_module_contains_no_send_path():
    """The Gmail scope grant permits sending. This asserts the code does not.

    Grepping source is crude, but the guarantee being made to the user is
    exactly 'there is no send call in here', so that is the thing to check.
    """
    source = Path(draft.__file__).read_text()
    for forbidden in ("messages().send", "drafts().send", ".send("):
        assert forbidden not in source, f"a send path appeared: {forbidden}"
    assert "drafts().create" in source, "drafts are still the only output"


# --- action lifecycle ----------------------------------------------------

class FakeConn:
    def __init__(self, row=None):
        self.sql, self.row = [], row

    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        return SimpleNamespace(lastrowid=1, fetchone=lambda: self.row)


def test_a_proposal_starts_unapproved():
    conn = FakeConn()
    draft.propose(conn, "draft_reply", 5, draft_of("body text"))
    _, params = conn.sql[0]
    assert params[3] == "proposed", "a draft must never start approved"
    assert json.loads(params[2])["body"] == "body text"


def test_an_unsure_verdict_produces_a_question_not_a_draft():
    conn = FakeConn()
    draft.flag(conn, 9, "could sit inside an existing deliverable")
    _, params = conn.sql[0]
    assert params[0] == "flag" and params[3] == "proposed"


def test_a_discarded_draft_can_be_restored():
    """The UI offers Restore on a discarded draft. Without 'undone' in the
    match, that button renders, posts, and silently changes nothing."""
    conn = FakeConn()
    draft.approve(conn, 1)
    sql, _ = conn.sql[0]
    assert "'undone'" in sql, "Restore would be a no-op"
    assert "'executed'" not in sql, "an action already in Gmail is not re-approved"


def test_pushing_an_unapproved_action_is_refused():
    conn = FakeConn(row={"type": "draft_reply", "payload": "{}",
                         "state": "proposed"})
    try:
        draft.push(conn, 1, service=None)
    except ValueError as e:
        assert "not approved" in str(e)
    else:
        raise AssertionError("unapproved action reached Gmail")


def test_undo_deletes_the_draft_it_created():
    deleted = []

    class Service:
        def users(self):
            return self

        def drafts(self):
            return self

        def delete(self, userId, id):
            deleted.append(id)
            return SimpleNamespace(execute=lambda: None)

    conn = FakeConn(row={"payload": json.dumps({"gmail_draft_id": "d1"}),
                         "state": "executed"})
    draft.undo(conn, 1, Service())
    assert deleted == ["d1"]
    assert "undone" in conn.sql[-1][0]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nall checks passed")
