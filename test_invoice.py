"""Checks for the invoice reader.

The failure that matters here has money attached in both directions: showing a
charge nobody billed, or clearing a charge nobody agreed to.

Run: .venv/bin/python test_invoice.py     (no API calls, no framework)
"""
from types import SimpleNamespace

import invoice
from invoice import Bill, Line, NotOnTheInvoice

INVOICE = """| 1 | Five-page static marketing website | INR 85,000 |
| 2 | Hindi version of the site: translation of all five pages | INR 18,000 |
| 3 | Online payment integration and checkout | INR 22,000 |
Total due: INR 125,000"""


def bill(*pairs):
    return Bill(lines=[Line(description=d, amount=a) for d, a in pairs])


# --- a line must actually be on the invoice ------------------------------

def test_verbatim_lines_pass():
    b = bill(("Five-page static marketing website", "INR 85,000"))
    assert invoice.validate_lines(b, INVOICE).lines[0].amount == "INR 85,000"


def test_rewrapped_description_passes():
    b = bill(("Hindi version of the site:\ntranslation of all five pages", "INR 18,000"))
    assert invoice.validate_lines(b, INVOICE)


def test_an_invented_charge_is_refused():
    # a line nobody billed, shown to someone deciding whether to pay
    b = bill(("Emergency weekend support", "INR 40,000"))
    try:
        invoice.validate_lines(b, INVOICE)
    except NotOnTheInvoice as e:
        assert "Emergency weekend support" in str(e)
    else:
        raise AssertionError("a charge that was never billed reached the reader")


def test_a_changed_amount_is_refused():
    # right description, wrong number — the most dangerous single edit
    b = bill(("Online payment integration and checkout", "INR 220,000"))
    try:
        invoice.validate_lines(b, INVOICE)
    except NotOnTheInvoice:
        pass
    else:
        raise AssertionError("an altered amount was accepted")


# --- amounts are read, never computed ------------------------------------

def test_amounts_are_parsed_from_what_was_written():
    assert invoice.amount_of("INR 85,000") == 85000
    assert invoice.amount_of("$1,250.50") == 1250.5


def test_an_unparseable_amount_returns_nothing_rather_than_a_guess():
    assert invoice.amount_of("to be agreed") is None
    assert invoice.amount_of("") is None
    assert invoice.amount_of(None) is None


# --- the three buckets ---------------------------------------------------

def verdict(covered, confidence="certain"):
    return SimpleNamespace(covered=covered, confidence=confidence,
                           scope_item_id=1, reasoning="r")


def test_lines_split_into_payable_challenged_and_undecided():
    lines = [Line(description="a", amount="INR 10"),
             Line(description="b", amount="INR 20"),
             Line(description="c", amount="INR 30")]
    got = invoice.totals(lines, [verdict(True), verdict(False),
                                 verdict(True, "unsure")])
    assert [l.amount for l, _ in got["payable"]] == ["INR 10"]
    assert [l.amount for l, _ in got["challenge"]] == ["INR 20"]
    assert [l.amount for l, _ in got["decide"]] == ["INR 30"]


def test_an_unsure_line_is_never_silently_cleared():
    """A charge the system is unsure about must not land in payable, whichever
    way `covered` happens to fall."""
    lines = [Line(description="a", amount="INR 10")]
    for covered in (True, False):
        got = invoice.totals(lines, [verdict(covered, "unsure")])
        assert not got["payable"], "an unsure charge was cleared for payment"
        assert not got["challenge"], "an unsure charge was disputed on its own"
        assert len(got["decide"]) == 1


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nall checks passed")
