"""SOW ingestion — the cold start.

Turns a contract into structured scope items, each carrying a VERBATIM span of
the source document. Every out-of-scope flag the agent later shows a user cites
one of these spans, so a fabricated quote is not a cosmetic problem: it is a
citation that points at nothing, discovered on camera.

Hence validate_quotes(): a quote that is not found in the source raises. It is
never dropped, never silently repaired.

Note: the API's `citations` feature is not used here — it returns 400 when
combined with output_config/structured output. Requiring the model to copy the
span into the schema gets the same result and is checkable.
"""
import re
import sys
from pathlib import Path

from pydantic import BaseModel

import db
import llm

SYSTEM = """You extract structured scope from a contract between a freelance \
developer and a client.

For every obligation, deliverable, exclusion, or commercial term in the \
document, emit one scope item.

Rules for source_quote:
- It MUST be copied character-for-character from the document.
- Do not paraphrase, summarise, correct spelling, fix punctuation, or join \
text from two different places.
- Quote the smallest span that carries the meaning — usually one sentence.

Categories:
- deliverable: work the developer owes the client
- exclusion: work explicitly NOT covered by this agreement
- term: commercial or process terms (fees, timeline, revisions, change control)

Exclusions matter as much as deliverables. A request that hits an exclusion is \
the clearest possible out-of-scope signal, so extract every one."""


class ScopeItem(BaseModel):
    item_text: str      # normalized statement of the obligation
    source_quote: str   # verbatim span of the SOW
    category: str       # deliverable | exclusion | term


class Sow(BaseModel):
    items: list[ScopeItem]


class QuoteNotFound(Exception):
    """A source_quote was not present in the document. Loud on purpose."""


def read_sow(path):
    """SOW as plain text. PDF goes through pypdf so the same text serves both
    the model and the validator — one source of truth for what 'verbatim' means."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        return "\n".join(p.extract_text() or "" for p in PdfReader(path).pages)
    return path.read_text()


def _norm(s):
    """Whitespace-insensitive comparison. A model that re-wraps a line has not
    invented anything; a model that changes a word has."""
    return re.sub(r"\s+", " ", s).strip()


def validate_quotes(items, sow_text):
    """Return the items whose quotes are absent. Caller decides what to do."""
    haystack = _norm(sow_text)
    return [i for i in items if _norm(i.source_quote) not in haystack]


def extract(sow_text, parse_fn=None, retries=1):
    parse_fn = parse_fn or llm.parse
    turns = [
        {
            "role": "user",
            "text": f"<document>\n{sow_text}\n</document>\n\n"
            "Extract every scope item from this document.",
        }
    ]

    for attempt in range(retries + 1):
        result = parse_fn(SYSTEM, turns, Sow)
        items = result.parsed.items
        bad = validate_quotes(items, sow_text)
        if not bad:
            return items

        if attempt == retries:
            raise QuoteNotFound(
                f"{len(bad)} of {len(items)} source_quote values are not in the "
                "document:\n"
                + "\n".join(f"  - {i.source_quote!r}" for i in bad)
            )

        # name the offenders; do not ask for a general retry
        turns += [
            {"role": "assistant", "text": result.parsed.model_dump_json()},
            {
                "role": "user",
                "text": "These source_quote values do not appear in the "
                "document:\n"
                + "\n".join(f"- {i.source_quote!r}" for i in bad)
                + "\n\nRe-extract every item. Copy each source_quote "
                "character-for-character from the document.",
            },
        ]


def store(conn, project_id, items):
    conn.execute("DELETE FROM scope_item WHERE project_id = ?", (project_id,))
    conn.executemany(
        "INSERT INTO scope_item (project_id, item_text, source_quote, category)"
        " VALUES (?,?,?,?)",
        [(project_id, i.item_text, i.source_quote, i.category) for i in items],
    )


def main():
    project_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    with db.connect() as conn:
        row = conn.execute(
            "SELECT sow_text FROM project WHERE id = ?", (project_id,)
        ).fetchone()
        if not row:
            sys.exit(f"no project {project_id} — run seed.py first")

        items = extract(row["sow_text"])
        store(conn, project_id, items)

    for i in items:
        print(f"[{i.category:11}] {i.item_text}")
        print(f"              ↳ {i.source_quote[:78]}")
    print(f"\n{len(items)} scope items, all quotes verified against the SOW")


if __name__ == "__main__":
    main()
