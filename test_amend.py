"""Checks for amendments — scope agreed by email after signature.

An amendment quietly widens what someone is owed. The two failures worth
catching are inventing agreement that was never given, and letting the wrong
party give it.

Run: .venv/bin/python test_amend.py     (no API calls, no framework)
"""
from types import SimpleNamespace

import amend
from amend import Amendment, NotAgreed

ACCEPTANCE = ("Discussed with my father and we're happy to go ahead with the "
              "Hindi version at INR 18,000 over two weeks. Please start it "
              "once the Collections page is done, not before.")

REQUEST = "We'd like the whole site in Hindi as well, switchable from the header."


def amendment(quote, text="Hindi version of the site", conditions=None):
    return Amendment(item_text=text, source_quote=quote, conditions=conditions)


# --- the agreement must actually have been given -------------------------

def test_verbatim_agreement_passes():
    q = "we're happy to go ahead with the Hindi version at INR 18,000 over two weeks"
    assert amend.validate(amendment(q), ACCEPTANCE).source_quote == q


def test_rewrapped_agreement_passes():
    q = "happy to go ahead with the Hindi version\nat INR 18,000 over two weeks"
    assert amend.validate(amendment(q), ACCEPTANCE)


def test_invented_agreement_is_refused():
    # fluent, plausible, never written — the scope-widening version of a
    # fabricated contract quote
    try:
        amend.validate(amendment("I approve the Hindi work as quoted"), ACCEPTANCE)
    except NotAgreed as e:
        assert "not in the accepting message" in str(e)
    else:
        raise AssertionError("scope widened on words nobody wrote")


def test_a_quote_from_the_request_is_refused():
    # the request is not agreement to the request
    try:
        amend.validate(amendment("switchable from the header"), ACCEPTANCE)
    except NotAgreed:
        pass
    else:
        raise AssertionError("asking for something counted as agreeing to it")


# --- only the paying side can widen scope --------------------------------

def verdict(accepts=13):
    return SimpleNamespace(accepts_change_to=accepts)


def test_the_client_can_agree():
    msg = {"from_counterparty": 1}
    assert amend.widens_scope(verdict(), msg, "contractor") is True


def test_the_contractor_cheering_is_not_agreement():
    """"Great, I'll start Monday" must never enlarge what is owed."""
    msg = {"from_counterparty": 0}
    assert amend.widens_scope(verdict(), msg, "contractor") is False


def test_the_sides_swap_when_read_from_the_buyer():
    # reading as the shop, WE are the paying side, so our own message agrees
    assert amend.widens_scope(verdict(), {"from_counterparty": 0}, "buyer") is True
    assert amend.widens_scope(verdict(), {"from_counterparty": 1}, "buyer") is False


def test_no_acceptance_no_amendment():
    assert amend.widens_scope(verdict(None), {"from_counterparty": 1},
                              "contractor") is False


# --- conditions survive --------------------------------------------------

class FakeConn:
    def __init__(self): self.rows = []
    def execute(self, sql, params):
        self.rows.append(params)
        return SimpleNamespace(lastrowid=1)


def test_a_condition_is_kept_with_the_item():
    """"Start it once the Collections page is done" changes what was agreed."""
    conn = FakeConn()
    a = amendment("we're happy to go ahead", conditions="only after the Collections page")
    amend.store(conn, 1, a, {"id": 9, "received_at": "2026-08-26T04:40:00Z"})
    _, item_text, quote, category, origin, msg_id, agreed = conn.rows[0]
    assert "only after the Collections page" in item_text
    assert origin == "amendment" and msg_id == 9


def test_the_amendment_records_which_message_agreed_it():
    conn = FakeConn()
    amend.store(conn, 1, amendment("we're happy to go ahead"),
                {"id": 42, "received_at": "2026-08-26T04:40:00Z"})
    assert conn.rows[0][5] == 42, "provenance must point at a real message"
    assert conn.rows[0][6] == "2026-08-26T04:40:00Z"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nall checks passed")
