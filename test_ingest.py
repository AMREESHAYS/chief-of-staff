"""Checks for the quote validator — the part that fails silently if it's wrong.

Run: python3 test_ingest.py     (no API calls, no framework)
"""
import llm
from ingest import QuoteNotFound, ScopeItem, Sow, extract, validate_quotes

SOW = """The contractor will build a five-page static marketing website
consisting of Home, Collections, About, Contact, and Store Locator pages.

Online payments, shopping cart, and any e-commerce functionality are
excluded from this agreement."""


def item(quote):
    return ScopeItem(item_text="x", source_quote=quote, category="deliverable")


def test_exact_quote_passes():
    assert validate_quotes([item("excluded from this agreement.")], SOW) == []


def test_rewrapped_quote_passes():
    # the model joined a line break into a space — no words changed
    q = "Online payments, shopping cart, and any e-commerce functionality are excluded"
    assert validate_quotes([item(q)], SOW) == []


def test_paraphrase_is_caught():
    # plausible, wrong, and the exact failure that puts an empty citation on screen
    bad = item("E-commerce features are not included in this agreement.")
    assert validate_quotes([bad], SOW) == [bad]


def test_single_word_swap_is_caught():
    bad = item("The contractor will build a six-page static marketing website")
    assert validate_quotes([bad], SOW) == [bad]


def test_only_bad_items_returned():
    good, bad = item("five-page static marketing website"), item("invented text")
    assert validate_quotes([good, bad], SOW) == [bad]


class FakeParse:
    """Stands in for llm.parse. Returns canned extractions, counts calls."""

    def __init__(self, *responses):
        self.responses, self.calls, self.turns = list(responses), 0, None

    def __call__(self, system, turns, schema):
        self.calls += 1
        self.turns = turns
        return llm.Result(parsed=self.responses.pop(0), cache_read_tokens=0,
                          provider="fake", model="fake")


def test_bad_extraction_raises_not_drops():
    fake = FakeParse(Sow(items=[item("invented"), item("five-page")]),
                     Sow(items=[item("still invented")]))
    try:
        extract(SOW, parse_fn=fake)
    except QuoteNotFound as e:
        assert "still invented" in str(e)
        assert fake.calls == 2, "should retry exactly once"
    else:
        raise AssertionError("bad quotes were dropped instead of raising")


def test_retry_recovers():
    fake = FakeParse(Sow(items=[item("invented")]),
                     Sow(items=[item("five-page static marketing website")]))
    assert len(extract(SOW, parse_fn=fake)) == 1
    assert fake.calls == 2
    # the retry must name the offender, not ask again vaguely
    assert "invented" in fake.turns[-1]["text"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nall checks passed")
