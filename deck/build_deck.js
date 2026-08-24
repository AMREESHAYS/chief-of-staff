const pptxgen = require("pptxgenjs");

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";            // 13.3 x 7.5
pres.author = "Chief of Staff";
pres.title = "Chief of Staff";

// the product's own palette: carbon-copy violet on cool photocopy stock
const CARBON = "4B3F9E";
const CARBON_DEEP = "2E2569";
const PAPER = "F2F3F7";
const PAPER_LIFT = "FBFBFD";
const INK = "16171D";
const SOFT = "5B6072";
const STAMP = "B3341F";
const STAMP_PALE = "FBEDEA";
const SETTLED = "2F6B4F";
const AMBER = "8A6516";
const AMBER_PALE = "FAF2DF";
const WHITE = "FFFFFF";

// three voices, as in the product: contract speaks serif, agent sans, record mono
const SERIF = "Cambria";
const SANS = "Calibri";
const MONO = "Courier New";

const W = 13.3;

function eyebrow(slide, text, x, y, color = CARBON) {
  slide.addText(text.toUpperCase(), {
    x, y, w: 8, h: 0.26, margin: 0,
    fontFace: MONO, fontSize: 11, color, charSpacing: 1.4,
  });
}

function title(slide, text, opts = {}) {
  slide.addText(text, {
    x: 0.7, y: opts.y || 0.72, w: opts.w || 11.9, h: opts.h || 1.0, margin: 0,
    fontFace: SERIF, fontSize: opts.size || 38, bold: true,
    color: opts.color || INK, valign: "top",
  });
}

// the motif: a passage lifted out of a signed document, set in serif
function excerpt(slide, quote, x, y, w, opts = {}) {
  const h = opts.h || 1.35;
  slide.addShape(pres.ShapeType.roundRect, {
    x, y, w, h, rectRadius: 0.04,
    fill: { color: opts.fill || PAPER_LIFT },
    line: { color: opts.line || "D6D9E3", width: 1 },
  });
  slide.addText(opts.label || "THE AGREEMENT THEY BOTH SIGNED", {
    x: x + 0.28, y: y + 0.16, w: w - 0.56, h: 0.24, margin: 0,
    fontFace: MONO, fontSize: 10, color: opts.labelColor || CARBON,
    charSpacing: 1.2,
  });
  slide.addText(quote, {
    x: x + 0.28, y: y + 0.46, w: w - 0.56, h: h - 0.66, margin: 0,
    fontFace: SERIF, fontSize: opts.size || 16, italic: true, color: INK,
    valign: "top", lineSpacing: opts.lineSpacing || 22,
  });
}

function card(slide, x, y, w, h, fill = PAPER_LIFT) {
  slide.addShape(pres.ShapeType.roundRect, {
    x, y, w, h, rectRadius: 0.03,
    fill: { color: fill },
    line: { color: "D6D9E3", width: 1 },
  });
}

function dot(slide, x, y, glyph, color = CARBON, d = 0.42) {
  slide.addShape(pres.ShapeType.ellipse, {
    x, y, w: d, h: d, fill: { color },
  });
  slide.addText(glyph, {
    x, y, w: d, h: d, margin: 0,
    fontFace: MONO, fontSize: 13, bold: true, color: WHITE,
    align: "center", valign: "middle",
  });
}

/* ---------------- 1 — title ---------------- */
{
  const s = pres.addSlide();
  s.background = { color: CARBON_DEEP };

  s.addText("Chief of Staff", {
    x: 0.9, y: 2.0, w: 11.5, h: 1.0, margin: 0,
    fontFace: SERIF, fontSize: 54, bold: true, color: WHITE,
  });

  s.addText("Gmail has your inbox.\nIt does not have your contract.", {
    x: 0.9, y: 3.15, w: 11.0, h: 1.5, margin: 0,
    fontFace: SERIF, fontSize: 30, italic: true, color: "CFC9F2",
    lineSpacing: 40,
  });

  s.addText("An agent that reads a freelancer's client email against the agreement they signed.", {
    x: 0.9, y: 4.95, w: 11.0, h: 0.4, margin: 0,
    fontFace: SANS, fontSize: 16, color: "A9A2D8",
  });

  s.addText("AI BUILDERS HACKATHON 2026", {
    x: 0.9, y: 6.5, w: 6, h: 0.3, margin: 0,
    fontFace: MONO, fontSize: 11, color: "7E76B8", charSpacing: 1.6,
  });
  s.addNotes("Open cold on the line. No logo, no preamble. It is an accurate technical claim, not a slogan: a mail-only tool cannot make this judgement because it has never read the contract.");
}

/* ---------------- 2 — the problem ---------------- */
{
  const s = pres.addSlide();
  s.background = { color: PAPER };
  eyebrow(s, "the freelancer's problem", 0.7, 0.42);
  title(s, "Two things go wrong, and neither lives in the inbox");

  const items = [
    ["Scope creep arrives politely", STAMP,
     "“Can you also just add…” Nobody says “I'd like to change the contract.” It reads as a favour, so it gets done for free."],
    ["Promises get lost in threads", CARBON,
     "“I'll get that to you by Friday” is buried under forty messages. The client remembers. You don't."],
  ];

  items.forEach(([head, color, body], i) => {
    const y = 2.05 + i * 1.95;
    card(s, 0.7, y, 7.4, 1.6);
    dot(s, 1.0, y + 0.32, i === 0 ? "!" : "?", color);
    s.addText(head, {
      x: 1.62, y: y + 0.26, w: 6.2, h: 0.36, margin: 0,
      fontFace: SANS, fontSize: 19, bold: true, color: INK,
    });
    s.addText(body, {
      x: 1.62, y: y + 0.68, w: 6.2, h: 0.8, margin: 0,
      fontFace: SANS, fontSize: 13.5, color: SOFT, lineSpacing: 19,
    });
  });

  card(s, 8.55, 2.05, 4.05, 3.55, CARBON_DEEP);
  s.addText("The cost", {
    x: 8.85, y: 2.35, w: 3.4, h: 0.3, margin: 0,
    fontFace: MONO, fontSize: 11, color: "A9A2D8", charSpacing: 1.4,
  });
  s.addText("Unpaid work,\nand a client who\nstops trusting\nyour dates.", {
    x: 8.85, y: 2.8, w: 3.5, h: 1.9, margin: 0,
    fontFace: SERIF, fontSize: 24, color: WHITE, lineSpacing: 33,
  });
  s.addText("Every freelancer eats both.", {
    x: 8.85, y: 4.95, w: 3.5, h: 0.4, margin: 0,
    fontFace: SANS, fontSize: 13, italic: true, color: "A9A2D8",
  });
  s.addNotes("Both problems are invisible to an inbox because the inbox has no idea what was agreed.");
}

/* ---------------- 3 — why it isn't already solved ---------------- */
{
  const s = pres.addSlide();
  s.background = { color: PAPER };
  eyebrow(s, "the obvious question", 0.7, 0.42);
  title(s, "Why hasn't Gmail already done this?");

  s.addText("Because triage tools read mail. This reads the contract first.", {
    x: 0.7, y: 1.82, w: 11.9, h: 0.4, margin: 0,
    fontFace: SANS, fontSize: 17, color: SOFT,
  });

  card(s, 0.7, 2.5, 5.75, 3.2, WHITE);
  s.addText("CLIENT, 10 AUGUST", {
    x: 1.0, y: 2.78, w: 5.0, h: 0.26, margin: 0,
    fontFace: MONO, fontSize: 10, color: SOFT, charSpacing: 1.2,
  });
  s.addText("“Customers keep calling to ask if they can just buy the rings directly from the website. Nothing fancy, just a buy button.”", {
    x: 1.0, y: 3.15, w: 5.15, h: 1.3, margin: 0,
    fontFace: SANS, fontSize: 15, color: INK, lineSpacing: 21,
  });
  s.addShape(pres.ShapeType.roundRect, {
    x: 1.0, y: 4.62, w: 1.75, h: 0.34, rectRadius: 0.03,
    fill: { color: STAMP_PALE }, line: { color: STAMP, width: 1 },
  });
  s.addText("OUT OF SCOPE", {
    x: 1.0, y: 4.62, w: 1.75, h: 0.34, margin: 0,
    fontFace: MONO, fontSize: 10, bold: true, color: STAMP,
    align: "center", valign: "middle",
  });

  excerpt(s, "“Online payments, shopping cart, and any e-commerce functionality are excluded from this agreement.”",
    6.85, 2.5, 5.75, { h: 2.2, size: 17 });
  s.addText("The agent quotes the clause into the reply, word for word, so the client can check it.", {
    x: 6.85, y: 4.9, w: 5.75, h: 0.8, margin: 0,
    fontFace: SANS, fontSize: 13.5, color: SOFT, lineSpacing: 19,
  });
  s.addNotes("This is the defensibility slide. A mail-only competitor cannot produce the right-hand side at all.");
}

/* ---------------- 4 — what it does ---------------- */
{
  const s = pres.addSlide();
  s.background = { color: PAPER };
  eyebrow(s, "what it does", 0.7, 0.42);
  title(s, "Read the contract. Judge the mail. Track the promises.");

  const cols = [
    ["1", "Reads the agreement", "Turns a signed contract into scope items, each carrying a span copied word for word. A quote that isn't in the document raises — it is never quietly dropped."],
    ["2", "Judges every message", "In scope, out of scope, a new commitment, or noise — with the clause that decides it and how sure it is."],
    ["3", "Keeps the ledger", "Every promise you made, the words that set its deadline, and whether you kept it."],
  ];

  cols.forEach(([n, head, body], i) => {
    const x = 0.7 + i * 4.1;
    card(s, x, 2.15, 3.75, 3.5);
    dot(s, x + 0.32, 2.5, n);
    s.addText(head, {
      x: x + 0.32, y: 3.12, w: 3.1, h: 0.7, margin: 0,
      fontFace: SANS, fontSize: 18, bold: true, color: INK, lineSpacing: 24,
    });
    s.addText(body, {
      x: x + 0.32, y: 3.9, w: 3.1, h: 1.6, margin: 0,
      fontFace: SANS, fontSize: 13, color: SOFT, lineSpacing: 18,
    });
  });

  s.addText("It acts, but only into drafts — and never sends.", {
    x: 0.7, y: 6.0, w: 11.9, h: 0.4, margin: 0,
    fontFace: SANS, fontSize: 15, italic: true, color: CARBON,
  });
  s.addNotes("Three capabilities, one dependency: all of it rests on having read the contract.");
}

/* ---------------- 5 — the proof beat ---------------- */
{
  const s = pres.addSlide();
  s.background = { color: PAPER };
  eyebrow(s, "the moment that sells it", 0.7, 0.42);
  title(s, "It flagged the slip a week before the client did");

  card(s, 0.7, 2.15, 5.75, 1.85, WHITE);
  s.addText("YOU · 12 AUGUST", {
    x: 1.0, y: 2.42, w: 5.0, h: 0.26, margin: 0,
    fontFace: MONO, fontSize: 10, color: CARBON, charSpacing: 1.2,
  });
  s.addText("“I'll get the Collections page to you by Friday.”", {
    x: 1.0, y: 2.78, w: 5.15, h: 0.9, margin: 0,
    fontFace: SANS, fontSize: 16, color: INK, lineSpacing: 22,
  });

  card(s, 0.7, 4.25, 5.75, 1.85, WHITE);
  s.addText("CLIENT · 19 AUGUST", {
    x: 1.0, y: 4.52, w: 5.0, h: 0.26, margin: 0,
    fontFace: MONO, fontSize: 10, color: STAMP, charSpacing: 1.2,
  });
  s.addText("“Any luck with the Collections page? You'd mentioned Friday…”", {
    x: 1.0, y: 4.88, w: 5.15, h: 0.9, margin: 0,
    fontFace: SANS, fontSize: 16, color: INK, lineSpacing: 22,
  });

  card(s, 6.85, 2.15, 5.75, 3.95, CARBON_DEEP);
  s.addText("THE LEDGER, ON 14 AUGUST", {
    x: 7.15, y: 2.5, w: 5.1, h: 0.28, margin: 0,
    fontFace: MONO, fontSize: 10, color: "A9A2D8", charSpacing: 1.3,
  });
  s.addText("OVERDUE", {
    x: 7.15, y: 2.95, w: 2.0, h: 0.32, margin: 0,
    fontFace: MONO, fontSize: 13, bold: true, color: "FF9C87", charSpacing: 1.2,
  });
  s.addText("Collections page\ndue 14 August\nnot delivered", {
    x: 7.15, y: 3.42, w: 5.1, h: 1.4, margin: 0,
    fontFace: SERIF, fontSize: 21, color: WHITE, lineSpacing: 30,
  });
  s.addText("Five days before the client asked.", {
    x: 7.15, y: 5.15, w: 5.1, h: 0.4, margin: 0,
    fontFace: SANS, fontSize: 14, italic: true, color: "A9A2D8",
  });
  s.addNotes("Nothing here was scripted for the demo. The link between the two messages is a field the classifier fills in: the 19 August message is conversationally noise and is also chasing a specific commitment.");
}

/* ---------------- 6 — how it works ---------------- */
{
  const s = pres.addSlide();
  s.background = { color: PAPER };
  eyebrow(s, "how it works", 0.7, 0.42);
  title(s, "Four stages, and a rule about who decides what");

  const steps = [
    ["INGEST", "Contract → scope items,\neach with a verbatim span"],
    ["CLASSIFY", "Message → verdict,\ncited to a clause"],
    ["LEDGER", "Copied words → dates,\nresolved in code"],
    ["DRAFT", "Proposal → your\napproval. Never sent."],
  ];

  steps.forEach(([head, body], i) => {
    const x = 0.7 + i * 3.08;
    card(s, x, 2.2, 2.75, 1.95);
    s.addText(head, {
      x: x + 0.24, y: 2.46, w: 2.3, h: 0.3, margin: 0,
      fontFace: MONO, fontSize: 12, bold: true, color: CARBON, charSpacing: 1.2,
    });
    s.addText(body, {
      x: x + 0.24, y: 2.88, w: 2.35, h: 1.05, margin: 0,
      fontFace: SANS, fontSize: 12.5, color: SOFT, lineSpacing: 17,
    });
    if (i < 3) {
      s.addText("→", {
        x: x + 2.79, y: 2.9, w: 0.3, h: 0.4, margin: 0,
        fontFace: SANS, fontSize: 18, color: CARBON, align: "center",
      });
    }
  });

  card(s, 0.7, 4.5, 5.9, 1.85, WHITE);
  s.addText("The model copies. The code decides.", {
    x: 1.0, y: 4.78, w: 5.3, h: 0.34, margin: 0,
    fontFace: SANS, fontSize: 17, bold: true, color: INK,
  });
  s.addText("It copies “by Friday” verbatim. Code anchors that to when the message arrived, in the freelancer's timezone. A model asked for a date invents a plausible one.",
    { x: 1.0, y: 5.2, w: 5.3, h: 1.0, margin: 0,
      fontFace: SANS, fontSize: 12.5, color: SOFT, lineSpacing: 17 });

  card(s, 6.9, 4.5, 5.7, 1.85, WHITE);
  s.addText("“Soon” is recorded, not discarded.", {
    x: 7.2, y: 4.78, w: 5.1, h: 0.34, margin: 0,
    fontFace: SANS, fontSize: 17, bold: true, color: INK,
  });
  s.addText("A promise with no date is the one that goes missing. It is kept in the ledger, marked vague, quoting the word you actually used.",
    { x: 7.2, y: 5.2, w: 5.1, h: 1.0, margin: 0,
      fontFace: SANS, fontSize: 12.5, color: SOFT, lineSpacing: 17 });
  s.addNotes("Python, SQLite, FastAPI. Provider behind one interface: Gemini shipped, Ollama for local iteration, Anthropic supported.");
}

/* ---------------- 7 — what it refuses to do ---------------- */
{
  const s = pres.addSlide();
  s.background = { color: PAPER };
  eyebrow(s, "trust", 0.7, 0.42);
  title(s, "What it refuses to do");
  s.addText("Three rails, enforced in code and covered by tests — not asked for in a prompt.", {
    x: 0.7, y: 1.8, w: 11.9, h: 0.4, margin: 0,
    fontFace: SANS, fontSize: 16, color: SOFT,
  });

  const rails = [
    ["Never sends", "Drafts are created and stop there. The Gmail scope would permit sending; the codebase has no send path, and a test asserts it."],
    ["Never invents money", "No fee or percentage may appear in a reply unless it is already in the contract."],
    ["Never picks your date", "A late-work update leaves [NEW DATE] for you. Committing you to a date you never agreed is not the agent's to do."],
  ];

  rails.forEach(([head, body], i) => {
    const y = 2.45 + i * 1.32;
    card(s, 0.7, y, 7.6, 1.12, WHITE);
    dot(s, 1.0, y + 0.34, "×", STAMP, 0.4);
    s.addText(head, {
      x: 1.6, y: y + 0.16, w: 6.4, h: 0.32, margin: 0,
      fontFace: SANS, fontSize: 16.5, bold: true, color: INK,
    });
    s.addText(body, {
      x: 1.6, y: y + 0.52, w: 6.5, h: 0.52, margin: 0,
      fontFace: SANS, fontSize: 12, color: SOFT, lineSpacing: 16,
    });
  });

  card(s, 8.7, 2.45, 3.9, 3.99, AMBER_PALE);
  s.addText("AND WHEN IT ISN'T SURE", {
    x: 9.0, y: 2.75, w: 3.3, h: 0.28, margin: 0,
    fontFace: MONO, fontSize: 10, color: AMBER, charSpacing: 1.2,
  });
  s.addText("“Could the logo animate a little?”", {
    x: 9.0, y: 3.2, w: 3.35, h: 1.0, margin: 0,
    fontFace: SERIF, fontSize: 17, italic: true, color: INK, lineSpacing: 24,
  });
  s.addText("Small enough that it might sit inside an existing deliverable. The agent writes no draft. It asks you.", {
    x: 9.0, y: 4.35, w: 3.35, h: 1.2, margin: 0,
    fontFace: SANS, fontSize: 12.5, color: SOFT, lineSpacing: 17,
  });
  s.addNotes("Undo is free precisely because nothing was sent — at worst a draft leaves the drafts folder.");
}

/* ---------------- 8 — validation ---------------- */
{
  const s = pres.addSlide();
  s.background = { color: PAPER };
  eyebrow(s, "did you overfit to your own demo?", 0.7, 0.42);
  title(s, "Two unrelated contracts. No prompt changes.");

  s.addText("The second was written to break the first one's assumptions: prose instead of lists, exclusions buried mid-paragraph under no heading, dollars instead of rupees, a timezone west of UTC.", {
    x: 0.7, y: 1.78, w: 11.9, h: 0.6, margin: 0,
    fontFace: SANS, fontSize: 14, color: SOFT, lineSpacing: 19,
  });

  const rows = [
    ["", "WEBSITE BUILD", "DATA PIPELINE"],
    ["Contract style", "sections, explicit list", "prose, buried in text"],
    ["Money / timezone", "INR fixed · +5:30", "USD hourly · −4:00"],
    ["Scope items extracted", "14", "13"],
    ["Unverifiable quotes", "0", "0"],
    ["Exclusions found", "3 of 3", "4 of 4"],
    ["Verdicts rejected", "0 of 20", "0 of 16"],
    ["“Unsure” verdicts", "1", "1"],
    ["Drafts refused by rails", "0", "0"],
  ];

  const tableRows = rows.map((r, i) => {
    const head = i === 0;
    return r.map((cell, c) => ({
      text: cell,
      options: {
        fontFace: head || c === 0 ? SANS : MONO,
        fontSize: head ? 10.5 : 12,
        bold: head,
        color: head ? CARBON : (c === 0 ? INK : SETTLED),
        align: c === 0 ? "left" : "center",
        charSpacing: head ? 1.1 : 0,
      },
    }));
  });

  s.addTable(tableRows, {
    x: 0.7, y: 2.62, w: 8.4, colW: [3.5, 2.45, 2.45],
    rowH: 0.34, border: { type: "solid", color: "D6D9E3", pt: 1 },
    fill: { color: WHITE }, valign: "middle", margin: 6,
  });

  card(s, 9.45, 2.62, 3.15, 3.06, CARBON_DEEP);
  s.addText("The unsure band held", {
    x: 9.72, y: 2.92, w: 2.65, h: 0.6, margin: 0,
    fontFace: SANS, fontSize: 15, bold: true, color: WHITE, lineSpacing: 20,
  });
  s.addText("“Could the logo animate?” and “just another column” are the same shape — and each was the single unsure verdict in its thread.", {
    x: 9.72, y: 3.66, w: 2.65, h: 1.8, margin: 0,
    fontFace: SANS, fontSize: 12, color: "CFC9F2", lineSpacing: 17,
  });
  s.addNotes("This pre-empts the most obvious skeptical question. Two domains, two currencies, two timezones, one prompt.");
}

/* ---------------- 9 — engineering ---------------- */
{
  const s = pres.addSlide();
  s.background = { color: PAPER };
  eyebrow(s, "how we know it works", 0.7, 0.42);
  title(s, "The second contract found three bugs");

  s.addText("Adding it was not a demo flourish. It was a test, and it failed usefully.", {
    x: 0.7, y: 1.8, w: 11.9, h: 0.4, margin: 0,
    fontFace: SANS, fontSize: 15, color: SOFT,
  });

  const bugs = [
    ["Dates off by one, west of UTC", "End-of-day stored in UTC reads as the next day at −4:00. A +5:30 demo hid it completely."],
    ["A page that rendered fine, with a piece missing", "Row ids assumed to be per-project were global, so replaying the second contract silently dropped every link between a chase and its promise."],
    ["One project's run wiping another's", "Same root cause. Once we saw the shape twice, we audited for it and found a third instance before it bit."],
  ];

  bugs.forEach(([head, body], i) => {
    const y = 2.45 + i * 1.3;
    card(s, 0.7, y, 8.0, 1.1, WHITE);
    dot(s, 1.0, y + 0.33, String(i + 1), i === 1 ? STAMP : CARBON, 0.4);
    s.addText(head, {
      x: 1.6, y: y + 0.15, w: 6.85, h: 0.3, margin: 0,
      fontFace: SANS, fontSize: 15.5, bold: true, color: INK,
    });
    s.addText(body, {
      x: 1.6, y: y + 0.5, w: 6.9, h: 0.52, margin: 0,
      fontFace: SANS, fontSize: 11.5, color: SOFT, lineSpacing: 15,
    });
  });

  card(s, 9.05, 2.45, 3.55, 3.9, WHITE);
  s.addText("67", {
    x: 9.35, y: 2.72, w: 3.0, h: 0.85, margin: 0,
    fontFace: SANS, fontSize: 54, bold: true, color: CARBON,
  });
  s.addText("checks, no framework", {
    x: 9.35, y: 3.6, w: 3.0, h: 0.3, margin: 0,
    fontFace: MONO, fontSize: 11, color: SOFT, charSpacing: 1.1,
  });
  s.addText("They cover what fails quietly: a fabricated quote, an invented price, a cache prefix that stops being stable, a due date one day out, a send path appearing where none should exist.", {
    x: 9.35, y: 4.15, w: 3.0, h: 2.0, margin: 0,
    fontFace: SANS, fontSize: 12, color: SOFT, lineSpacing: 17,
  });
  s.addNotes("The bug worth dwelling on is the second: it rendered a valid-looking page with a piece missing. Nothing signals check this.");
}

/* ---------------- 10 — product & close ---------------- */
{
  const s = pres.addSlide();
  s.background = { color: CARBON_DEEP };

  s.addText("WHO PAYS, AND WHY", {
    x: 0.9, y: 0.7, w: 8, h: 0.3, margin: 0,
    fontFace: MONO, fontSize: 11, color: "7E76B8", charSpacing: 1.5,
  });
  s.addText("One recovered change order pays for a year", {
    x: 0.9, y: 1.15, w: 11.5, h: 0.9, margin: 0,
    fontFace: SERIF, fontSize: 36, bold: true, color: WHITE,
  });

  const facts = [
    ["Who", "Freelance developers and consultants who bill by scope — people who lose money to unpaid extras, not to lack of leads."],
    ["Model", "Per-seat subscription. Paid inference, not a free tier: a real statement of work carries client names and fees."],
    ["Built", "Contract ingestion, message classification, obligation ledger, drafting with safety rails, and the review surface — running end to end on two contracts."],
  ];

  facts.forEach(([head, body], i) => {
    const x = 0.9 + i * 3.95;
    s.addText(head, {
      x, y: 2.75, w: 3.5, h: 0.32, margin: 0,
      fontFace: MONO, fontSize: 12, bold: true, color: "A9A2D8", charSpacing: 1.3,
    });
    s.addText(body, {
      x, y: 3.2, w: 3.55, h: 1.9, margin: 0,
      fontFace: SANS, fontSize: 13, color: "E4E1F6", lineSpacing: 19,
    });
  });

  s.addText("Gmail has your inbox. It does not have your contract.", {
    x: 0.9, y: 5.7, w: 11.5, h: 0.6, margin: 0,
    fontFace: SERIF, fontSize: 25, italic: true, color: "CFC9F2",
  });
  s.addNotes("Close on the same line the deck opened with.");
}

pres.writeFile({ fileName: "deck/chief-of-staff.pptx" })
  .then(f => console.log("wrote", f));
