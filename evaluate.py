"""Measure the classifier instead of asserting it.

Two questions, and the second is the one this project learned the hard way:

  1. Does it agree with a human reading of the same message?
  2. Does it agree with ITSELF across runs?

A claim backed by one run says nothing about the second question, and this
system has already shipped a claim that was true once and not in general. So
`--runs N` re-classifies each message N times and reports how often the answer
moved. A verdict that is right 60% of the time is not 60% right; it is
unreliable, and the number that says so belongs in the report.

Ground truth lives beside the messages in the fixtures, written by reading the
contract rather than by recording what the system happens to output. Where two
readings are genuinely defensible the label carries `also`; where a human
should decide, it carries `ambiguous`, and the correct behaviour is `unsure`.

    evaluate.py                 score the run already in the database, free
    evaluate.py --runs 3        re-classify live, three times, report stability
"""
import argparse
import json
import sys
from collections import Counter, defaultdict

import classify
import db
import ledger
from seed import load_fixture

FIXTURES = ["fixtures/thread.json", "fixtures/pipeline.json"]


def labels_for(fixture):
    """Expected verdicts, keyed by arrival time so they survive a reseed."""
    data, _ = load_fixture(fixture)
    return {
        m["at"]: {
            "expect": m["expect"],
            "also": set(m.get("also", [])),
            "ambiguous": m.get("ambiguous", False),
            "accepts": m.get("expect_accepts_change", False),
            "cites": m.get("expect_cites"),
            "body": m["body"],
        }
        for m in data["messages"] if "expect" in m
    }


def project_for(conn, fixture):
    data, _ = load_fixture(fixture)
    row = conn.execute("SELECT id FROM project WHERE client_name = ?",
                       (data["project"]["client_name"],)).fetchone()
    return row["id"] if row else None


def stored_verdicts(conn, project_id):
    return {
        r["received_at"]: dict(r)
        for r in conn.execute(
            "SELECT m.received_at, v.label, v.confidence, v.accepts_change_to,"
            " s.origin, s.source_quote, s.item_text"
            " FROM verdict v JOIN message m ON m.id = v.message_id"
            " JOIN thread t ON t.id = m.thread_id"
            " LEFT JOIN scope_item s ON s.id = v.scope_item_id"
            " WHERE t.project_id = ?", (project_id,))
    }


def score(labels, verdicts):
    """One run against the labels. Returns counts and the disagreements."""
    out = Counter()
    misses = []
    for at, want in labels.items():
        got = verdicts.get(at)
        if not got:
            out["unscored"] += 1
            continue
        out["scored"] += 1

        if got["label"] == want["expect"]:
            out["exact"] += 1
            out["acceptable"] += 1
        elif got["label"] in want["also"]:
            out["acceptable"] += 1
            out["second_reading"] += 1
        else:
            misses.append((at, want, got))

        # the decision with money attached, counted on its own
        if want["expect"] == "out_of_scope":
            out["oos_actual"] += 1
            if got["label"] == "out_of_scope":
                out["oos_found"] += 1
        elif got["label"] == "out_of_scope" and "out_of_scope" not in want["also"]:
            out["oos_false"] += 1

        # escalation: does unsure land where a human should decide
        if want["ambiguous"]:
            out["ambiguous"] += 1
            if got["confidence"] == "unsure":
                out["escalated"] += 1
        elif got["confidence"] == "unsure":
            out["unsure_elsewhere"] += 1

        if want["accepts"]:
            out["acceptances"] += 1
            if got["accepts_change_to"]:
                out["acceptances_caught"] += 1
        if want["cites"]:
            out["cite_expected"] += 1
            if got["origin"] == want["cites"]:
                out["cite_correct"] += 1
    return out, misses


def citations_hold(conn, project_id):
    """Every out-of-scope verdict must cite a clause whose quote is really in
    the document it claims. Enforced at write time; measured here anyway,
    because the claim is worth checking from the other end."""
    from ingest import _norm

    sow = conn.execute("SELECT sow_text FROM project WHERE id = ?",
                       (project_id,)).fetchone()["sow_text"]
    bad = 0
    total = 0
    for r in conn.execute(
        "SELECT s.source_quote, s.origin, m.body FROM verdict v"
        " JOIN message m ON m.id = v.message_id"
        " JOIN thread t ON t.id = m.thread_id"
        " JOIN scope_item s ON s.id = v.scope_item_id"
        " LEFT JOIN message om ON om.id = s.origin_message_id"
        " WHERE t.project_id = ? AND v.label = 'out_of_scope'", (project_id,)
    ):
        total += 1
        if _norm(r["source_quote"]) not in _norm(sow):
            bad += 1
    return total, bad


def live_runs(conn, project_id, times):
    """Re-classify every message `times` over, and report where the answer
    moved. Costs quota — this is the expensive, honest measurement."""
    project = conn.execute(
        "SELECT owner_tz, my_role FROM project WHERE id = ?",
        (project_id,)).fetchone()
    items = [dict(r) for r in conn.execute(
        "SELECT id, item_text, source_quote, category, origin, agreed_at"
        " FROM scope_item WHERE project_id = ? ORDER BY id", (project_id,))]
    messages = [dict(r) for r in conn.execute(
        "SELECT m.* FROM message m JOIN thread t ON t.id = m.thread_id"
        " WHERE t.project_id = ? ORDER BY m.received_at", (project_id,))]

    seen = defaultdict(list)
    for run in range(times):
        for n, target in enumerate(messages):
            try:
                verdict, _ = classify.classify(
                    target, messages[:n], items, (), tz=project["owner_tz"],
                    role=project["my_role"])
                seen[target["received_at"]].append(verdict.label)
            except Exception as e:
                seen[target["received_at"]].append(f"error:{type(e).__name__}")
        print(f"    run {run + 1} of {times} done", flush=True)
    return seen


def report(name, counts, misses, citations, stability=None):
    scored = counts["scored"] or 1
    print(f"\n{name}")
    print("-" * 66)
    print(f"  exact agreement          {counts['exact']:>3}/{scored}"
          f"   {counts['exact'] / scored:.0%}")
    print(f"  defensible reading       {counts['acceptable']:>3}/{scored}"
          f"   {counts['acceptable'] / scored:.0%}"
          f"   (+{counts['second_reading']} second readings)")

    found, actual = counts["oos_found"], counts["oos_actual"]
    print(f"  out-of-scope found       {found:>3}/{actual}"
          f"   {found / (actual or 1):.0%}"
          f"   {counts['oos_false']} false")

    if counts["ambiguous"]:
        print(f"  ambiguous escalated      {counts['escalated']:>3}"
              f"/{counts['ambiguous']}"
              f"   {counts['unsure_elsewhere']} unsure elsewhere")
    if counts["acceptances"]:
        print(f"  acceptance detected      {counts['acceptances_caught']:>3}"
              f"/{counts['acceptances']}")
    if counts["cite_expected"]:
        print(f"  cites the amendment      {counts['cite_correct']:>3}"
              f"/{counts['cite_expected']}")

    total, bad = citations
    print(f"  citations verifiable     {total - bad:>3}/{total}"
          + ("   <-- FABRICATED CITATION" if bad else ""))

    if stability is not None:
        agree, n = stability
        print(f"  same answer every run    {agree:>3}/{n}"
              f"   {agree / (n or 1):.0%}")

    for at, want, got in misses:
        print(f"\n  disagreement · {at[:10]}")
        print(f"    expected {want['expect']}, got {got['label']}"
              f" ({got['confidence']})")
        print(f"    {want['body'][:70]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=0,
                    help="re-classify live this many times and report stability")
    args = ap.parse_args()

    totals = Counter()
    with db.connect() as conn:
        for fixture in FIXTURES:
            project_id = project_for(conn, fixture)
            if not project_id:
                print(f"{fixture}: not loaded, skipping")
                continue
            labels = labels_for(fixture)
            counts, misses = score(labels, stored_verdicts(conn, project_id))
            totals.update(counts)

            stability = None
            if args.runs:
                seen = live_runs(conn, project_id, args.runs)
                agree = sum(1 for v in seen.values() if len(set(v)) == 1)
                stability = (agree, len(seen))
                totals["stable"] += agree
                totals["stable_of"] += len(seen)

            report(fixture, counts, misses,
                   citations_hold(conn, project_id), stability)

    print("\n" + "=" * 66)
    scored = totals["scored"] or 1
    print(f"  {totals['exact']}/{scored} exact"
          f" · {totals['acceptable']}/{scored} defensible"
          f" · {totals['oos_found']}/{totals['oos_actual']} out-of-scope found"
          f" · {totals['oos_false']} false")
    if totals["stable_of"]:
        print(f"  {totals['stable']}/{totals['stable_of']} messages answered"
              f" identically across {args.runs} runs")
    return 0 if totals["oos_false"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
