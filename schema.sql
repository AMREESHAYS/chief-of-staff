PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS project (
    id            INTEGER PRIMARY KEY,
    client_name   TEXT NOT NULL,
    owner_tz      TEXT NOT NULL DEFAULT 'Asia/Kolkata',  -- anchor for due-date resolution
    sow_filename  TEXT,
    sow_text      TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

-- ground truth. source_quote is a VERBATIM span of project.sow_text;
-- every citation the agent shows a user comes from here.
CREATE TABLE IF NOT EXISTS scope_item (
    id            INTEGER PRIMARY KEY,
    project_id    INTEGER NOT NULL REFERENCES project(id),
    item_text     TEXT NOT NULL,
    source_quote  TEXT NOT NULL,
    category      TEXT
);

CREATE TABLE IF NOT EXISTS thread (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES project(id),
    gmail_thread_id TEXT UNIQUE,
    subject         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message (
    id           INTEGER PRIMARY KEY,
    thread_id    INTEGER NOT NULL REFERENCES thread(id),
    gmail_msg_id TEXT UNIQUE,
    sender       TEXT NOT NULL,
    from_client  INTEGER NOT NULL,          -- 1 = client wrote it, 0 = we did
    received_at  TEXT NOT NULL,             -- ISO 8601 UTC. due-date anchor.
    body         TEXT NOT NULL
);

-- classifier output, one row per message
CREATE TABLE IF NOT EXISTS verdict (
    id            INTEGER PRIMARY KEY,
    message_id    INTEGER NOT NULL UNIQUE REFERENCES message(id),
    label         TEXT NOT NULL CHECK (label IN
                    ('in_scope','out_of_scope','new_commitment','noise')),
    scope_item_id INTEGER REFERENCES scope_item(id),  -- NULL for noise
    reasoning     TEXT NOT NULL,
    -- a band, not a probability: models pick reliably between named options
    -- and self-report continuous confidence badly. 'unsure' is the escalation
    -- gate, so it has to be reachable.
    confidence    TEXT NOT NULL CHECK (confidence IN
                    ('certain','likely','unsure')),
    -- orthogonal to label, same principle as promise_text: a message can be
    -- noise AND be the client chasing a commitment made three weeks ago.
    references_obligation_id INTEGER REFERENCES obligation(id),
    -- chases: asking about it. fulfils: delivering it. Without this every
    -- delivered promise stays open and the ledger reports false overdues.
    obligation_relation TEXT CHECK (obligation_relation IN ('chases','fulfils'))
);

CREATE TABLE IF NOT EXISTS obligation (
    id             INTEGER PRIMARY KEY,
    project_id     INTEGER NOT NULL REFERENCES project(id),
    message_id     INTEGER NOT NULL REFERENCES message(id),
    promise_text   TEXT NOT NULL,
    due_phrase     TEXT,                    -- verbatim, e.g. "by Friday". NULL if none.
    due_at         TEXT,                    -- resolved UTC, NULL when vague/absent
    due_confidence REAL,
    status         TEXT NOT NULL CHECK (status IN
                     ('open','done','overdue','vague')),
    created_at     TEXT NOT NULL
);

-- audit log AND undo. one table, not two.
CREATE TABLE IF NOT EXISTS action (
    id          INTEGER PRIMARY KEY,
    type        TEXT NOT NULL CHECK (type IN ('draft_reply','nudge','flag')),
    target_id   INTEGER NOT NULL,           -- message.id or obligation.id per type
    payload     TEXT NOT NULL,              -- JSON: draft body, gmail draft id, etc.
    state       TEXT NOT NULL CHECK (state IN
                  ('proposed','approved','executed','undone')),
    created_at  TEXT NOT NULL,
    executed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_message_thread ON message(thread_id, received_at);
CREATE INDEX IF NOT EXISTS idx_obligation_status ON obligation(project_id, status);
CREATE INDEX IF NOT EXISTS idx_action_target ON action(type, target_id);
