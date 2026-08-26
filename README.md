# Chief of Staff

**Gmail has your inbox. It does not have your contract.**

An agent that reads client email against the agreement that was signed, and
answers two questions an inbox cannot:

1. **Is this request inside what we agreed?** — and if not, *which line* says so.
2. **What did I promise, and did I do it?**

Every flag cites a verbatim span of the contract. Every promise is tracked with
the words that set its deadline. Nothing is ever sent.

---

## Why the contract matters

Inbox triage tools summarise, prioritise, and draft replies. None of them can
tell a freelancer that a request is out of scope, because none of them have
read the statement of work. That document is the ground truth this system runs
on, and it is why the output is checkable rather than merely plausible:

> **Client, 10 Aug:** *"Customers keep calling to ask if they can just buy the
> rings directly from the website. Nothing fancy, just a buy button."*
>
> **out of scope** — the agreement you both signed:
> *"Online payments, shopping cart, and any e-commerce functionality are
> excluded from this agreement."*

---

## What it does

**Ingest** parses the contract into scope items, each carrying a span copied
character-for-character from the source. A span that isn't in the document
raises rather than being dropped — a citation that points at nothing is worse
than a failed import.

**Classify** reads each message against those scope items and returns a label
(`in_scope` / `out_of_scope` / `new_commitment` / `noise`), the clause that
decides it, and a confidence band. Two further fields are independent of the
label, because one message often does two things at once:

- `promise_text` — a request can be out of scope *and* be answered with a
  commitment. That pairing is the worst case and both halves are kept.
- `references_obligation_id` + `chases` / `fulfils` — "any luck with that?" is
  conversationally noise *and* is the client chasing a specific overdue
  promise.

**Ledger** tracks both sides' promises, tagged with who made them, and turns
the copied words into dates. Resolution happens in code, not
in the model: the classifier copies "by Friday", and the ledger anchors it to
when the message arrived, in the project owner's timezone. A promise with no
resolvable date is stored as `vague`, not discarded — "soon" is exactly the
commitment that goes missing.

**Draft** proposes a change-order reply for confidently out-of-scope requests,
a proactive update for overdue promises, and *a question instead of a draft*
when the classifier was unsure.

**Amend** watches for the client agreeing, in writing, to work the contract did
not cover — and writes that agreement into scope. See below; it is the part
that keeps the system from being wrong a week after it was right.

---

## What it refuses to do

Three rails, enforced in code and covered by tests — not asked for in a prompt.

| Rail | Enforced by |
|---|---|
| A change-order reply must contain the contract line it relies on, verbatim, in the body the client reads | `validate_change_order` |
| No fee, price, or percentage may appear unless it is already in the contract | `invented_figures` |
| A late-work update must leave `[NEW DATE]` for the developer rather than commit them to a date | `validate_nudge` |
| An update still carrying an unfilled date cannot be approved at all | `draft.needs_a_date` |
| Only the paying side can widen the scope; a contractor's enthusiasm is not agreement | `amend.widens_scope` |
| An amendment must quote the accepting message verbatim | `amend.validate` |

A draft that breaks a rail is regenerated once with the violation named, then
refused and counted. **No draft is better than an unsafe one.**

**Nothing is sent.** Drafts are created through `users.drafts().create` and
stop there. The granted Gmail scope (`gmail.compose`) *would* permit sending —
there is no draft-only scope — so the guarantee is that this codebase contains
no send path, and a test asserts it.

---

## Validated on two unrelated contracts

The second contract was written to be hostile to the first one's prompts: prose
instead of lists, exclusions buried mid-paragraph under no heading naming them,
hourly USD billing instead of a fixed INR fee, and a timezone west of UTC.

**No prompt changes between them.**

| | Meridian Jewellers (website build) | Northwind Freight (data pipeline) |
|---|---|---|
| Contract style | numbered sections, explicit exclusions list | prose, exclusions inside paragraphs |
| Money | INR, fixed fee | USD, hourly with an hours cap |
| Timezone | Asia/Kolkata (+5:30) | America/New_York (−4) |
| Scope items extracted | 14 | 13 |
| Unverifiable quotes | 0 | 0 |
| Exclusions found | 3 of 3 | 4 of 4 |
| Messages classified | 20 | 16 |
| Verdicts rejected | 0 | 0 |
| `unsure` verdicts | 1 | 1 |
| Obligation tiebacks | 3 | 4 |
| Drafts refused by rails | 0 | 0 |

### The confidence band, and how it was nearly wrong

An earlier version of this file claimed the `unsure` band "held across
domains" on the strength of a single run. Re-running the same message gave
`certain`, `unsure`, `unsure` — even at temperature 0. The claim described one
observation, not the system.

The cause was a design flaw rather than noise. Both contracts contain a
catch-all clause — *"any work not described in Section 1 requires a written
change order"*. With a catch-all present nothing is ever ambiguous: any small
unmentioned request can be called certainly out of scope by citing it. The
model was not wrong, but the answer was useless, because the band exists to
route genuine judgement calls to a human.

The rule now: **a catch-all clause is not evidence about a specific request.**
Certainty requires a clause about the subject matter of the request itself.

Verified after the change at five consecutive runs per message, temperature 0:
**10 of 10 `unsure`** across both contracts. *"Could the logo animate a
little?"* and *"Same data, just another column"* are structural twins, and each
is now reliably the single `unsure` verdict in its thread.

The second contract also exposed three defects the first could not reach, all
the same shape: **an id space assumed to be per-project when it is global.**

1. Due dates stored as end-of-day UTC read back one day late anywhere west of
   UTC. `+5:30` hid it; `−4:00` did not.
2. Snapshot references travelled as global obligation row ids, so replaying a
   second project silently lost every tieback — a page that rendered fine with
   a piece missing.
3. `draft.py` and the review surface both worked on all projects' actions
   rather than one.

An audit for that pattern found the third before it was hit.

---

## The contract is not frozen at signature

Scope changes by email. Somebody asks for work the agreement never covered, a
change order answers it, a reply says yes — and from that moment the work *is*
in scope. A system still citing the original contract is now wrong, and
"changes agreed by email that nobody wrote down" is the most commonly cited
cause of scope disputes.

So an agreed change becomes a scope item like any other:

| | |
|---|---|
| **17 Aug** — *"we'd like the whole site in Hindi"* | `out of scope`, citing the translation exclusion |
| **25 Aug** — a change order goes out | still out of scope; nothing has been agreed |
| **26 Aug** — *"we're happy to go ahead at INR 18,000 over two weeks"* | **scope changes here** |
| **27 Aug** — *"can Hindi prices use Devanagari numerals?"* | **`in scope`** — under the amendment |

What gets recorded:

> The contractor will translate five pages into Hindi, add a language switch to
> the header, and test both over a two-week period. **(Work must begin only
> after the Collections page is completed.)**
>
> *their words: "we're happy to go ahead with the Hindi version at INR 18,000
> over two weeks."*

It kept the condition they attached. Agreeing on terms is not the same as
agreeing, and an amendment that drops the terms overstates what was settled.

Two rules stop this becoming a licence to invent scope:

- **Only the paying side can widen it.** A contractor writing "great, I'll
  start Monday" is enthusiasm. Enforced in code, because a model asked whether
  someone agreed will find agreement in warmth.
- **The citation is a verbatim span of the accepting message**, checked against
  that message exactly as clause quotes are checked against the contract.

This generalises the original invariant rather than breaking it: every scope
item still cites a real span of a real document. Only *which* document varies.

Detection is asked as its own question rather than as one field among nine in
the verdict, because measurement showed the wide schema answered it
unreliably. Five runs each:

| | read as acceptance |
|---|---|
| *"we're happy to go ahead… at INR 18,000 over two weeks"* | 5/5 |
| *"Sure, let me look into what that would take"* | 0/5 |
| *"can you confirm the delivery date?"* | 0/5 |

The middle row is the one that matters — it is the shape that would quietly
enlarge what somebody is owed.

---

## Measured, not asserted

```bash
.venv/bin/python evaluate.py            # score the captured runs, free
.venv/bin/python evaluate.py --runs 3   # re-classify live, report stability
```

Expected verdicts live beside the messages in the fixtures, written by reading
the contract rather than by recording what the system outputs. Where two
readings are genuinely defensible the label says so; where a human should
decide, it is marked ambiguous and the correct behaviour is `unsure`.

```
28/39 exact · 37/39 defensible · 11/11 out-of-scope found · 0 false
```

`--runs N` re-classifies each message N times and reports how often the answer
moved, because this project has already shipped a claim that was true once and
not in general. A verdict that is right sixty percent of the time is not sixty
percent right; it is unreliable, and the number that says so belongs in the
report.

The harness found a bug on its first run that ninety-three unit tests had
missed: a Northwind message citing a clause from **Meridian's** contract.
Snapshots stored scope-item ids, which are global and shift on every reseed, so
a replayed verdict cited whatever now occupied that row. Every existing test
worked on a single project and structurally could not see it.

---

## Either side of the contract

A freelancer owes the work. A shop that hired one is owed it. Same contract,
same thread, opposite reading of who is late.

Whether a request is in scope turned out to be a property of the request, not
of who is reading it, so verdicts are side-neutral: a captured run replays
correctly from either perspective without reclassification. Only obligation
attribution depends on the side, and that derives from who sent the message.

`project.my_role` is `contractor` or `buyer`. The ledger splits into what you
owe and what they owe you. Late work becomes two different letters — an update
you send because you slipped, or a chase because they did. Apologising to a
supplier for their own delay is worse than sending nothing.

An out-of-scope request that *you* made produces a warning rather than a
drafted reply. There is nobody to reply to, and drafting one writes a letter in
the other side's voice and addresses it to you.

The repository ships all three views of two contracts:

```bash
.venv/bin/python seed.py --replay                                        # contractor
.venv/bin/python seed.py --fixture fixtures/pipeline.json --add --replay # contractor
.venv/bin/python seed.py --fixture fixtures/meridian_shop.json --add --replay  # buyer
```

The third replays the *same captured run* as the first. Same twenty verdicts,
same clause citations, opposite ledger.

## Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Set a provider key in `.env` (never committed):

```bash
printf 'Paste key (input hidden): '; read -rs key; echo; printf 'GEMINI_API_KEY=%s\n' "$key" >> .env; unset key; chmod 600 .env
```

Load the demo data and open the review surface:

```bash
.venv/bin/python seed.py --replay
.venv/bin/python seed.py --fixture fixtures/pipeline.json --add --replay
.venv/bin/python app.py 8000
```

`--replay` restores a captured run from fixtures, so the UI works with **zero
API calls**. To run the pipeline for real instead:

```bash
LLM_PROVIDER=gemini .venv/bin/python ingest.py 1
LLM_PROVIDER=gemini .venv/bin/python classify.py 1
LLM_PROVIDER=gemini .venv/bin/python draft.py 1
```

### Providers

| `LLM_PROVIDER` | Use |
|---|---|
| `ollama` (default) | local, unmetered — for iteration. **Not** evidence about classification quality; a 7B model rejects roughly half its own verdicts. |
| `gemini` | the shipped provider (`gemini-3.6-flash`) |
| `anthropic` | alternative (`claude-opus-5`) |

Provider-specific shapes live only in `llm.py`. Everything else passes a plain
system string and a list of turns.

### Gmail

`auth.py` requests `gmail.readonly` + `gmail.compose` and nothing else. Put an
OAuth desktop client at `credentials.json`, then:

```bash
.venv/bin/python auth.py
```

`seed.py --gmail` files the demo messages into the account with
`users.messages.insert`, which does not send mail.

---

## Tests

```bash
for t in test_llm test_ingest test_classify test_ledger test_draft test_amend; do
  .venv/bin/python $t.py
done
```

93 checks across six suites. No framework, no fixtures, no API calls. They cover the things that fail
*quietly*: a fabricated contract quote, an invented price, a cache prefix that
stops being stable, a due date one day out, a send path appearing in
`draft.py`.

---

## The one field it will not fill

An update about late work leaves the date blank, and the page renders that
blank as a real date picker rather than as text. **Approve stays disabled until
a person chooses one** — the point of leaving it empty is that a human decides,
and approving around that would put the literal placeholder in front of a
client.

The chosen date is stored beside the draft rather than written into it, so the
agent's words keep `[NEW DATE]` for the life of the record. The audit trail
still shows that the agent declined to pick a date and a person did.

---

## Demo data is fictional

Meridian Jewellers and Northwind Freight do not exist, and every address is
under `.example`, a reserved domain. The contracts and correspondence were
written for this project.

This matters beyond tidiness: the repository is public and the threads simulate
contract disputes. Publishing a real client's name attached to fabricated
emails they never wrote would not be ours to do.

The same reasoning applies to the product. A real statement of work carries
client names and fees, so this runs on paid inference rather than a free tier
that trains on submitted data.

---

## Limits

- One thread per project. Multi-thread and multi-client inboxes are not built.
- The buyer view reuses a run captured from the contractor's side, so it
  carries the contractor's promises but not the buyer's own. Both sides are
  tracked when a thread is classified fresh.
- Approving an action marks it approved. Pushing it to Gmail is a separate
  explicit call, and it creates a draft.
- Prompt caching is only meaningful on providers that expose it. The system
  prompt is kept byte-stable and the volatile half sits below the breakpoint,
  but Gemini caches implicitly and reports nothing, so no cache figure is
  claimed anywhere.

---

## Layout

```
schema.sql     data model; `action` is audit log and undo in one table
llm.py         one structured-output call, three providers
ingest.py      contract -> scope items with verbatim spans
classify.py    message -> verdict, against the scope items
ledger.py      copied words -> dates; what is owed and what is late
amend.py       agreement by email -> scope, cited to their own words
draft.py       proposals, and the rails that refuse unsafe ones
evaluate.py    score the classifier against labelled threads
app.py         the landing page and the review surface
seed.py        fixtures, snapshot, replay
```
