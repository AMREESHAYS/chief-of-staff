PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS project (
    id            INTEGER PRIMARY KEY,
    user_id       INTEGER REFERENCES user(id),
    client_name   TEXT NOT NULL,          -- the other party, whoever they are
    -- which side of this contract the user is on. A freelancer owes the work;
    -- a shop that hired one is owed it. Same contract, same thread, opposite
    -- reading of who is late.
    my_role       TEXT NOT NULL DEFAULT 'contractor'
                  CHECK (my_role IN ('contractor','buyer')),
    owner_tz      TEXT NOT NULL DEFAULT 'Asia/Kolkata',  -- anchor for due-date resolution
    sow_filename  TEXT,
    sow_text      TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

-- ground truth. source_quote is always a VERBATIM span of a real document —
-- of project.sow_text for a clause that was signed, and of the message body
-- for one the parties agreed later in writing. Which document it must be
-- checked against is what `origin` records.
--
-- A contract is not frozen at signature. It is amended by email, and an
-- amendment nobody wrote down is the single most common cause of a scope
-- dispute, so an agreed change becomes a scope item like any other.
CREATE TABLE IF NOT EXISTS scope_item (
    id            INTEGER PRIMARY KEY,
    project_id    INTEGER NOT NULL REFERENCES project(id),
    item_text     TEXT NOT NULL,
    source_quote  TEXT NOT NULL,
    category      TEXT,
    origin        TEXT NOT NULL DEFAULT 'contract'
                  CHECK (origin IN ('contract','amendment')),
    -- the message that agreed it, for an amendment. NULL for signed clauses.
    origin_message_id INTEGER REFERENCES message(id),
    agreed_at     TEXT
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
    -- 1 = the other party wrote it, 0 = we did. Not "from_client": when the
    -- user is the buyer, the counterparty is their supplier.
    from_counterparty INTEGER NOT NULL,
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
    obligation_relation TEXT CHECK (obligation_relation IN ('chases','fulfils')),
    -- the out-of-scope request this message agreed to, if any
    accepts_change_to INTEGER REFERENCES message(id)
);

CREATE TABLE IF NOT EXISTS obligation (
    id             INTEGER PRIMARY KEY,
    project_id     INTEGER NOT NULL REFERENCES project(id),
    message_id     INTEGER NOT NULL REFERENCES message(id),
    promise_text   TEXT NOT NULL,
    -- 'me' = the user promised it, 'them' = the counterparty did. A freelancer
    -- mostly cares about the first; a shop chasing a vendor cares about the
    -- second. Both are tracked, never mixed.
    owed_by        TEXT NOT NULL DEFAULT 'me' CHECK (owed_by IN ('me','them')),
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

-- A Gmail account the user has connected through the browser. The refresh
-- token lives here and nowhere else: never in a cookie, never in a URL, never
-- in the page. Deleting the row is the whole of disconnecting.
CREATE TABLE IF NOT EXISTS account (
    email        TEXT PRIMARY KEY,
    token        TEXT NOT NULL,
    scopes       TEXT NOT NULL,
    connected_at TEXT NOT NULL
);

-- A person who pays for this. Passwords are never stored: what is kept is a
-- scrypt hash and the salt it used, which cannot be turned back into the
-- password the person typed.
CREATE TABLE IF NOT EXISTS user (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    salt          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    display_name  TEXT,
    created_at    TEXT NOT NULL,
    onboarded_at  TEXT              -- NULL until they finish the welcome
);

-- A signed-in browser. The cookie holds a random token; this table holds only
-- its hash, so reading the database does not hand anyone a live session.
CREATE TABLE IF NOT EXISTS session (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_user ON session(user_id);
