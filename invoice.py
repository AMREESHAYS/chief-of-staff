"""Check a bill against what was actually agreed.

The same question the classifier asks of a message, asked of a line on an
invoice: is this covered? The answer has three shapes rather than two, because
by the time a bill arrives the agreement may have moved:

    covered by the contract    a clause of the signed document says so
    covered by an amendment    it was agreed by email afterwards, and the
                               words that agreed it are on record
    not covered                nothing covers it, and nobody agreed to it

The middle case is why this belongs in a system that tracks amendments. A line
for translation work is outside the signed contract and perfectly payable
anyway, because the client wrote "we're happy to go ahead" on the 26th. Without
that, the honest answer to every amended line would be a false accusation.

Two rails, both borrowed from the rest of the system:

  * a line's description and amount are copied verbatim from the invoice and
    checked against it, so the reader is never shown a charge nobody billed;
  * a verdict must cite a scope item that exists, and a challenge cites the
    exclusion it rests on.
"""
import re
import sys

from pydantic import BaseModel

import db
import llm
from ingest import _norm

MODEL_NOTE = "amounts are never computed here; totals come from the invoice"

EXTRACT_SYSTEM = """You are given an invoice. List the charges on it.

description — the wording of the line item, copied character for character \
from the invoice. Not summarised, not tidied.

amount — the amount charged for that line, copied exactly as written, \
including its currency: "INR 18,000", not 18000.

Include every charged line. Do not include totals, subtotals, tax lines, \
payment instructions, or notes — only the things being charged for."""

CHECK_SYSTEM = """You decide whether one line on an invoice is covered by what \
the parties agreed.

The scope items below are the whole of the agreement. Some came from the signed \
contract; those marked as agreed by email were added later when the client \
accepted a change in writing, and they carry exactly the same weight. Work \
covered by either is payable.

<scope_items>
{scope_items}
</scope_items>

covered — true if a scope item covers this charge, false if none does.

scope_item_id — the item that decides it. Required either way: when the charge \
is covered, the item that covers it; when it is not, the exclusion or \
deliverable that shows it was never included. Null only if genuinely nothing \
in the agreement speaks to this charge at all.

reasoning — one sentence for the person who has to pay or send this. Never \
mention item numbers or ids; refer to what the clause says.

confidence — certain, likely, or unsure. Use unsure when the line could \
reasonably be read as part of an existing deliverable or as extra work, and \
the agreement does not settle it. A catch-all clause about work not described \
in the agreement is not enough to be certain about a specific charge; it is \
what you fall back on when nothing speaks to it, which is exactly the case a \
human should decide."""


class Line(BaseModel):
    description: str
    amount: str


class Bill(BaseModel):
    lines: list[Line]


class LineVerdict(BaseModel):
    covered: bool
    scope_item_id: int | None
    reasoning: str
    confidence: str


class NotOnTheInvoice(Exception):
    """A line that is not in the document it was supposedly read from."""


def validate_lines(bill, invoice_text):
    """Every line must be findable in the invoice. A charge the reader is shown
    but was never billed is the same class of fabrication as an invented
    clause."""
    haystack = _norm(invoice_text)
    missing = [l for l in bill.lines
               if _norm(l.description) not in haystack
               or _norm(l.amount) not in haystack]
    if missing:
        raise NotOnTheInvoice(
            f"{len(missing)} line(s) are not in the invoice: "
            + "; ".join(f"{l.description!r} @ {l.amount!r}" for l in missing))
    return bill


def read(invoice_text, parse_fn=None, retries=1):
    parse_fn = parse_fn or llm.parse
    turns = [{"role": "user",
              "text": f"<invoice>\n{invoice_text}\n</invoice>\n\nList the charges."}]
    for attempt in range(retries + 1):
        result = parse_fn(EXTRACT_SYSTEM, turns, Bill)
        try:
            return validate_lines(result.parsed, invoice_text).lines
        except NotOnTheInvoice as e:
            if attempt == retries:
                raise
            turns = turns + [
                {"role": "assistant", "text": result.parsed.model_dump_json()},
                {"role": "user",
                 "text": f"Rejected: {e}\n\nCopy each description and amount "
                         "character for character from the invoice."},
            ]


def check(line, items, parse_fn=None):
    """One line against the agreement as it now stands."""
    import classify

    parse_fn = parse_fn or llm.parse
    result = parse_fn(
        CHECK_SYSTEM.format(scope_items=classify.render_scope(items)),
        [{"role": "user",
          "text": f"<line>\n{line.description}\n{line.amount}\n</line>\n\n"
                  "Is this charge covered?"}],
        LineVerdict,
    )
    v = result.parsed
    if v.confidence not in classify.BANDS:
        raise ValueError(f"unknown confidence band {v.confidence!r}")
    if v.scope_item_id is not None and v.scope_item_id not in {i["id"] for i in items}:
        raise ValueError(f"scope_item_id {v.scope_item_id} is not in this project")
    return v


def totals(lines, verdicts):
    """What is payable, what is challenged, and what a human must decide.

    Amounts are summed from the strings on the invoice. Nothing is computed
    from a model's arithmetic.
    """
    buckets = {"payable": [], "challenge": [], "decide": []}
    for line, v in zip(lines, verdicts):
        if v.confidence == "unsure":
            buckets["decide"].append((line, v))
        elif v.covered:
            buckets["payable"].append((line, v))
        else:
            buckets["challenge"].append((line, v))
    return buckets


def amount_of(text):
    """The numeric part of an amount as written. Returns None rather than a
    guess when the string is not a plain figure."""
    digits = re.sub(r"[^\d.]", "", text or "")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def run(project_id=1, path="fixtures/meridian_invoice.md"):
    from pathlib import Path

    invoice_text = Path(path).read_text()
    with db.connect() as conn:
        items = [dict(r) for r in conn.execute(
            "SELECT id, item_text, source_quote, category, origin, agreed_at"
            " FROM scope_item WHERE project_id = ? ORDER BY id", (project_id,))]
    if not items:
        sys.exit("no scope items — run ingest.py first")

    lines = read(invoice_text)
    verdicts = [check(l, items) for l in lines]
    by = {i["id"]: i for i in items}

    for line, v in zip(lines, verdicts):
        cited = by.get(v.scope_item_id)
        if v.confidence == "unsure":
            mark = "?  decide  "
        elif v.covered:
            mark = "+  payable "
        else:
            mark = "!  query   "
        source = ""
        if cited:
            source = ("  [agreed by email " + cited["agreed_at"][:10] + "]"
                      if cited["origin"] == "amendment" else "  [contract]")
        print(f"{mark} {line.amount:>12}  {line.description[:52]}{source}")
        print(f"              {v.reasoning[:88]}")

    b = totals(lines, verdicts)
    for name in ("payable", "challenge", "decide"):
        total = sum(amount_of(l.amount) or 0 for l, _ in b[name])
        print(f"\n{name:>10}: {len(b[name])} line(s), {total:,.0f}")


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
