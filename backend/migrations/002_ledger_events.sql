-- Independent ledger: bill numbers correspond to source_rows.source_key.
CREATE TABLE IF NOT EXISTS ledger_events (
    id BIGSERIAL PRIMARY KEY,
    bill_no VARCHAR(40) NOT NULL,
    version INTEGER NOT NULL,
    kind VARCHAR(20) NOT NULL,
    amount_cents BIGINT NOT NULL DEFAULT 0,
    occurred_at VARCHAR(64) NOT NULL DEFAULT '',
    memo VARCHAR(200) NOT NULL DEFAULT '',
    content_hash VARCHAR(64) NOT NULL,
    batch_key VARCHAR(120) NOT NULL,
    created_at VARCHAR(64) NOT NULL,
    CONSTRAINT ledger_event_version_uq UNIQUE (bill_no, version)
);
CREATE INDEX IF NOT EXISTS ledger_events_batch_idx ON ledger_events (batch_key);

CREATE TABLE IF NOT EXISTS ledger_bills (
    id BIGSERIAL PRIMARY KEY,
    bill_no VARCHAR(40) NOT NULL UNIQUE,
    applied_version INTEGER NOT NULL DEFAULT 0,
    amount_cents BIGINT NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    cancel_version INTEGER,
    version_count INTEGER NOT NULL DEFAULT 0,
    max_version INTEGER NOT NULL DEFAULT 0,
    updated_at VARCHAR(64) NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_bills_status_idx ON ledger_bills (status);

CREATE TABLE IF NOT EXISTS ledger_imports (
    id BIGSERIAL PRIMARY KEY,
    batch_key VARCHAR(120) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'processing',
    content_hash VARCHAR(64) NOT NULL,
    payload JSON NOT NULL,
    received_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    pending_count INTEGER NOT NULL DEFAULT 0,
    applied_advance INTEGER NOT NULL DEFAULT 0,
    bills_affected INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    rejected_reason VARCHAR(400) NOT NULL DEFAULT '',
    summary JSON NOT NULL DEFAULT '{}',
    actor VARCHAR(160) NOT NULL,
    source VARCHAR(200) NOT NULL DEFAULT '',
    created_at VARCHAR(64) NOT NULL,
    completed_at VARCHAR(64) NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ledger_imports_status_idx ON ledger_imports (status);
