"""Checks for the quote validator — the part that fails silently if it's wrong.

Run: python3 test_ingest.py     (no API calls, no framework)
"""
from types import SimpleNamespace

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


class FakeClient:
    """Returns canned extractions. Records how many calls it saw."""

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), 0
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(parsed_output=self.responses.pop(0))


def test_bad_extraction_raises_not_drops():
    client = FakeClient(Sow(items=[item("invented"), item("five-page")]),
                        Sow(items=[item("still invented")]))
    try:
        extract(SOW, client=client)
    except QuoteNotFound as e:
        assert "still invented" in str(e)
        assert client.calls == 2, "should retry exactly once"
    else:
        raise AssertionError("bad quotes were dropped instead of raising")


def test_retry_recovers():
    client = FakeClient(Sow(items=[item("invented")]),
                        Sow(items=[item("five-page static marketing website")]))
    assert len(extract(SOW, client=client)) == 1
    assert client.calls == 2


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nall checks passed")
