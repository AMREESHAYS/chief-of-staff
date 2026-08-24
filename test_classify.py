"""Checks for the classifier's cache shape and verdict validation.

The cache tests are the ones with money attached: if the system block is not
byte-identical across calls, every classification pays full input price and
nothing visibly breaks.

Run: .venv/bin/python test_classify.py     (no API calls, no framework)
"""
import llm
from classify import (LABELS, Verdict, build_messages, build_system,
                      classify, render_scope, validate)

ITEMS = [
    {"id": 3, "item_text": "Five-page static site", "category": "deliverable",
     "source_quote": "a five-page static marketing website"},
    {"id": 1, "item_text": "E-commerce excluded", "category": "exclusion",
     "source_quote": "any e-commerce functionality are excluded"},
    {"id": 2, "item_text": "Contact form", "category": "deliverable",
     "source_quote": "A contact form will be included"},
]

MSGS = [
    {"id": 10, "from_client": 1, "received_at": "2026-08-10T05:15:00Z",
     "body": "can we add a buy button"},
    {"id": 11, "from_client": 0, "received_at": "2026-08-10T11:30:00Z",
     "body": "sure, let me look into it"},
    {"id": 12, "from_client": 1, "received_at": "2026-08-11T06:00:00Z",
     "body": "sending photos"},
]


OBLIGATIONS = [
    {"id": 4, "promise_text": "Collections page by Friday", "due_phrase": "by Friday",
     "due_at": "2026-08-14T18:29:59+00:00", "status": "open",
     "created_at": "2026-08-12T13:20:00Z"},
]


def verdict(**kw):
    base = {"label": "noise", "scope_item_id": None, "reasoning": "r",
            "confidence": "certain", "promise_text": None, "due_phrase": None,
            "references_obligation_id": None}
    return Verdict(**{**base, **kw})


# --- cache shape ---------------------------------------------------------

def test_system_is_identical_across_messages():
    # the whole point: 20 classifications, one cached prefix
    assert build_system(ITEMS) == build_system(list(reversed(ITEMS)))


def test_scope_rendering_is_order_independent():
    # rows arriving in a different order must not shift a single byte
    assert render_scope(ITEMS) == render_scope(sorted(ITEMS, key=lambda i: i["item_text"]))


def test_nothing_volatile_leaks_into_system():
    text = build_system(ITEMS)
    for m in MSGS:
        assert m["body"] not in text, "thread content above the breakpoint kills the cache"
        assert m["received_at"] not in text
    # obligations change per message — they belong below the breakpoint too
    assert OBLIGATIONS[0]["promise_text"] not in text


def test_open_commitments_ride_in_the_volatile_half():
    turns = build_messages(MSGS[:1], MSGS[1], OBLIGATIONS)
    assert "Collections page by Friday" in turns[0]["text"]
    assert "[4]" in turns[0]["text"], "the id must be visible to be referenced"


def test_messages_carry_the_volatile_half():
    turns = build_messages(MSGS[:1], MSGS[1])
    content = turns[0]["text"]
    assert MSGS[1]["body"] in content
    assert MSGS[0]["body"] in content, "prior turn should be visible as context"
    assert turns[0]["role"] == "user"


def test_future_messages_are_not_visible():
    # classifying message 2 must not see message 3 — the developer didn't
    content = build_messages(MSGS[:1], MSGS[1])[0]["text"]
    assert MSGS[2]["body"] not in content


# --- verdict validation --------------------------------------------------

def test_dangling_scope_item_id_raises():
    try:
        validate(verdict(label="out_of_scope", scope_item_id=99), ITEMS)
    except ValueError as e:
        assert "99" in str(e)
    else:
        raise AssertionError("citation pointing at nothing was accepted")


def test_scope_verdict_without_citation_raises():
    try:
        validate(verdict(label="out_of_scope", scope_item_id=None), ITEMS)
    except ValueError as e:
        assert "no scope_item_id" in str(e)
    else:
        raise AssertionError("uncited out_of_scope verdict was accepted")


def test_unknown_confidence_band_raises():
    try:
        validate(verdict(confidence="0.9"), ITEMS)
    except ValueError as e:
        assert "confidence band" in str(e)
    else:
        raise AssertionError("a float snuck in where a band belongs")


def test_dangling_obligation_reference_raises():
    try:
        validate(verdict(references_obligation_id=42), ITEMS, OBLIGATIONS)
    except ValueError as e:
        assert "42" in str(e)
    else:
        raise AssertionError("reference to a nonexistent commitment accepted")


def test_reference_survives_a_noise_label():
    # the Aug 19 beat: "any luck with the Collections page?" is
    # conversationally noise AND is the client chasing an overdue promise.
    v = validate(verdict(label="noise", references_obligation_id=4),
                 ITEMS, OBLIGATIONS)
    assert v.label == "noise" and v.references_obligation_id == 4


def test_unknown_label_raises():
    try:
        validate(verdict(label="escalate"), ITEMS)
    except ValueError as e:
        assert "escalate" in str(e)
    else:
        raise AssertionError("invalid label accepted")


def test_noise_needs_no_citation():
    assert validate(verdict(label="noise"), ITEMS).scope_item_id is None


def test_promise_survives_an_out_of_scope_label():
    # the Aug 10 beat: an unguarded yes to an out-of-scope ask. Both halves
    # must be recorded, or the ledger misses the worst commitment in the thread.
    v = validate(verdict(label="out_of_scope", scope_item_id=1,
                         promise_text="let me look into it",
                         due_phrase=None), ITEMS)
    assert v.promise_text == "let me look into it"
    assert v.label == "out_of_scope"


def test_classify_rejects_a_bad_verdict_before_it_is_stored():
    def fake(system, turns, schema):
        return llm.Result(parsed=verdict(label="out_of_scope", scope_item_id=77),
                          cache_read_tokens=0, provider="fake", model="fake")
    try:
        classify(MSGS[0], [], ITEMS, parse_fn=fake)
    except ValueError as e:
        assert "77" in str(e)
    else:
        raise AssertionError("dangling citation reached the caller")


def test_labels_match_the_db_constraint():
    # schema.sql CHECK and this module must not drift apart
    import re
    from pathlib import Path
    sql = Path(__file__).parent.joinpath("schema.sql").read_text()
    declared = set(re.findall(r"'(in_scope|out_of_scope|new_commitment|noise)'", sql))
    assert declared == LABELS, f"schema {declared} vs classifier {LABELS}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nall checks passed")
