-- GST verification (TRUST_PLAN.md §4, §9 — phase T2, 2026-10-31).
--
-- gst_record (001) stays the append-only history of every check. What a check MEANS for
-- the grade depends on where it came from: only `live_gstn` (the provider called GSTN for
-- this request) can make a firm "GST verified". `vendor_cache` is "on record". `stub` is
-- the T2 stand-in that exercises the plumbing and can never verify anything
-- (stub_never_verifies) — the one class of row that may be deleted, by `make gst-stub-purge`,
-- because it was never a fact.

ALTER TABLE gst_record ADD COLUMN IF NOT EXISTS provider_class TEXT;
ALTER TABLE gst_record ADD COLUMN IF NOT EXISTS raw_sha256 TEXT;          -- raw/s16_gstin/ payload
ALTER TABLE gst_record ADD COLUMN IF NOT EXISTS einvoice_enabled BOOLEAN;
ALTER TABLE gst_record ADD COLUMN IF NOT EXISTS filing_frequency TEXT;    -- monthly | qrmp | composition; PRIVATE
ALTER TABLE gst_record ADD COLUMN IF NOT EXISTS trigger TEXT;             -- render | detail | shortlist | contact | rfq | claim | free_check | batch
ALTER TABLE gst_record ADD COLUMN IF NOT EXISTS cost_inr NUMERIC(10, 2);
ALTER TABLE gst_record ADD COLUMN IF NOT EXISTS latency_ms INT;
ALTER TABLE gst_record ADD COLUMN IF NOT EXISTS name_match BOOLEAN;       -- legal or trade name vs our record
ALTER TABLE gst_record ADD COLUMN IF NOT EXISTS state_match BOOLEAN;      -- principal place vs our record
ALTER TABLE gst_record ADD COLUMN IF NOT EXISTS payload JSONB;            -- normalised to GSTN field names
DO $$ BEGIN
  ALTER TABLE gst_record ADD CONSTRAINT gst_record_provider_class
    CHECK (provider_class IN ('live_gstn', 'vendor_cache', 'stub'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE INDEX IF NOT EXISTS gst_record_latest ON gst_record (gstin, verified_at DESC);

-- Name search (§3 rung 3). A candidate links only through the auto-link rule (gst/link.py)
-- or a human; anything weaker waits here as `review`. Stub candidates never reach
-- crosswalk or review_queue: a fake GSTIN must not cost a reviewer a minute.
CREATE TABLE IF NOT EXISTS gstin_candidate (
  id BIGSERIAL PRIMARY KEY,
  entity_id TEXT NOT NULL,
  gstin TEXT NOT NULL,
  legal_name TEXT, trade_name TEXT, pincode TEXT, state TEXT,
  provider TEXT, provider_class TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('auto_link', 'review', 'reject')),
  why TEXT,
  searched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS gstin_candidate_entity ON gstin_candidate (entity_id);

-- The budget guard's ledger (§4.4). Reconciled monthly against vendor invoices.
CREATE TABLE IF NOT EXISTS verification_spend (
  id BIGSERIAL PRIMARY KEY,
  at TIMESTAMPTZ NOT NULL DEFAULT now(),
  provider TEXT NOT NULL, provider_class TEXT NOT NULL,
  call TEXT NOT NULL,                 -- verify | search
  gstin TEXT, entity_id TEXT, trigger TEXT,
  cost_inr NUMERIC(10, 2) NOT NULL DEFAULT 0,
  ok BOOLEAN NOT NULL
);
CREATE INDEX IF NOT EXISTS verification_spend_at ON verification_spend (at);
