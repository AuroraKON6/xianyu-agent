-- 0001_core_write_domains.up.sql  (taskbook R10)
-- Authoritative write-domain schema: account composite keys, optimistic
-- revisions, unique constraints, foreign keys, NUMERIC money (tri-state
-- columns mirror the Money contract; amount is NULL unless state='known').
-- Money is NEVER stored as float.

CREATE TABLE IF NOT EXISTS accounts (
    account_id  TEXT PRIMARY KEY,
    channel     TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- ---------------- research domain ----------------

CREATE TABLE IF NOT EXISTS offer_snapshots (
    record_id      TEXT PRIMARY KEY,
    account_id     TEXT NOT NULL DEFAULT '',
    channel        TEXT NOT NULL DEFAULT '',
    provider_id    TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    source_sku_id  TEXT NOT NULL DEFAULT '',
    observed_at    TIMESTAMPTZ NOT NULL,
    fetched_at     TIMESTAMPTZ,
    price_state    TEXT NOT NULL DEFAULT 'unknown'
                   CHECK (price_state IN ('known','unknown','not_applicable')),
    price_amount   NUMERIC(20,6),
    price_currency TEXT NOT NULL DEFAULT 'CNY',
    price_reason   TEXT NOT NULL DEFAULT '',
    payload        JSONB NOT NULL,
    payload_hash   TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, channel, provider_id, source_item_id, source_sku_id, payload_hash)
);
CREATE INDEX IF NOT EXISTS idx_offers_provider_item
    ON offer_snapshots (provider_id, source_item_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS product_matches (
    record_id        TEXT PRIMARY KEY,
    left_snapshot_id TEXT NOT NULL REFERENCES offer_snapshots(record_id),
    right_snapshot_id TEXT NOT NULL REFERENCES offer_snapshots(record_id),
    grade            TEXT NOT NULL,
    revision         INTEGER NOT NULL DEFAULT 1,
    payload          JSONB NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_matches_pair
    ON product_matches (left_snapshot_id, right_snapshot_id, created_at DESC);

CREATE TABLE IF NOT EXISTS opportunities (
    record_id     TEXT PRIMARY KEY,
    canonical_sku TEXT NOT NULL,
    source        TEXT NOT NULL,
    account_id    TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL,
    revision      INTEGER NOT NULL DEFAULT 1,
    payload       JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (canonical_sku, source)
);

CREATE TABLE IF NOT EXISTS decision_records (
    record_id      TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES opportunities(record_id),
    actor_role     TEXT NOT NULL DEFAULT '',
    decision_type  TEXT NOT NULL DEFAULT '',
    payload        JSONB NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_decisions_opportunity
    ON decision_records (opportunity_id, created_at DESC);

-- ---------------- execution domain ----------------

CREATE TABLE IF NOT EXISTS agent_jobs (
    record_id       TEXT PRIMARY KEY,
    role            TEXT NOT NULL,
    status          TEXT NOT NULL,
    attempt         INTEGER NOT NULL DEFAULT 0,
    fencing_token   INTEGER NOT NULL DEFAULT 0,
    lease_owner     TEXT NOT NULL DEFAULT '',
    lease_until     TIMESTAMPTZ,
    lease_claimed_at TIMESTAMPTZ,
    account_id      TEXT NOT NULL DEFAULT '',
    write_required  BOOLEAN NOT NULL DEFAULT FALSE,
    idempotency_key TEXT NOT NULL DEFAULT '',
    deadline        TIMESTAMPTZ,
    revision        INTEGER NOT NULL DEFAULT 1,
    payload         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_jobs_idem
    ON agent_jobs (idempotency_key) WHERE idempotency_key <> '';

CREATE TABLE IF NOT EXISTS work_orders (
    record_id  TEXT PRIMARY KEY,
    account_id TEXT NOT NULL DEFAULT '',
    action     TEXT NOT NULL,
    status     TEXT NOT NULL,
    payload    JSONB NOT NULL,
    revision   INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS execution_jobs (
    job_id        TEXT PRIMARY KEY,
    work_order_id TEXT NOT NULL REFERENCES work_orders(record_id),
    action        TEXT NOT NULL,
    status        TEXT NOT NULL,
    lease_owner   TEXT NOT NULL DEFAULT '',
    lease_until   TIMESTAMPTZ,
    attempt       INTEGER NOT NULL DEFAULT 0,
    payload       JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- transactional outbox: written in the SAME transaction as the business
-- intent; a separate worker drains it (exactly-once by intent_key)
CREATE TABLE IF NOT EXISTS outbox (
    record_id  TEXT PRIMARY KEY,
    intent_key TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL DEFAULT '',
    payload    JSONB NOT NULL,
    state      TEXT NOT NULL DEFAULT 'pending'
               CHECK (state IN ('pending','sent','failed','cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at    TIMESTAMPTZ
);

-- ---------------- fulfilment domain ----------------

-- composite business identity: the same external order id on two
-- accounts (or channels) coexists instead of colliding
CREATE TABLE IF NOT EXISTS sales_orders (
    record_id   TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL,
    channel     TEXT NOT NULL,
    external_id TEXT NOT NULL,
    status      TEXT NOT NULL,
    revision    INTEGER NOT NULL DEFAULT 1,
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, channel, external_id)
);

CREATE TABLE IF NOT EXISTS procurement_orders (
    record_id        TEXT PRIMARY KEY,
    account_id       TEXT NOT NULL,
    sales_order_id   TEXT REFERENCES sales_orders(record_id),
    supplier_ref     TEXT NOT NULL DEFAULT '',
    supplier_order_id TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL,
    total_state      TEXT NOT NULL DEFAULT 'unknown',
    total_amount     NUMERIC(20,6),
    total_currency   TEXT NOT NULL DEFAULT 'CNY',
    payload          JSONB NOT NULL,
    revision         INTEGER NOT NULL DEFAULT 1,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, supplier_order_id)
);

CREATE TABLE IF NOT EXISTS budget_reservations (
    reservation_key TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL,
    bucket          TEXT NOT NULL,
    business_day    TEXT NOT NULL,
    amount_state    TEXT NOT NULL DEFAULT 'known',
    amount          NUMERIC(20,6),
    currency        TEXT NOT NULL DEFAULT 'CNY',
    state           TEXT NOT NULL DEFAULT 'reserved'
                    CHECK (state IN ('reserved','settled','released','orphaned')),
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    settled_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_budget_window
    ON budget_reservations (account_id, bucket, business_day);

CREATE TABLE IF NOT EXISTS inventory_locks (
    lock_key      TEXT PRIMARY KEY,
    account_id    TEXT NOT NULL,
    sku           TEXT NOT NULL,
    quantity      INTEGER NOT NULL CHECK (quantity > 0),
    procurement_id TEXT REFERENCES procurement_orders(record_id),
    sales_order_id TEXT REFERENCES sales_orders(record_id),
    state         TEXT NOT NULL DEFAULT 'held'
                  CHECK (state IN ('held','released','consumed','orphaned')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_inventory_locks_sku
    ON inventory_locks (account_id, sku, state);

-- ---------------- finance domain (append-only) ----------------
-- Idempotency: (source_event_id, entry_type) is unique; a replayed
-- evidence event with a different amount is REJECTED, not merged.
-- History is never edited: corrections are reversing entries.
CREATE TABLE IF NOT EXISTS ledger_entries (
    entry_id        TEXT PRIMARY KEY,
    entry_type      TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('credit','debit')),
    account_id      TEXT NOT NULL,
    sales_order_id  TEXT REFERENCES sales_orders(record_id),
    procurement_id  TEXT REFERENCES procurement_orders(record_id),
    amount_state    TEXT NOT NULL DEFAULT 'known',
    amount          NUMERIC(20,6),
    currency        TEXT NOT NULL DEFAULT 'CNY',
    evidence_ref    TEXT NOT NULL DEFAULT '',
    source_event_id TEXT NOT NULL DEFAULT '',
    reversal_of     TEXT REFERENCES ledger_entries(entry_id),
    payload         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_event_id, entry_type)
);
CREATE INDEX IF NOT EXISTS idx_ledger_account
    ON ledger_entries (account_id, currency, created_at DESC);
