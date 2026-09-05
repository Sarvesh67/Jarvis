-- Cosmetics supply-chain graph — Postgres staging + provenance layer.
-- Lives in the `cosmetics` database inside the shared jarvis-postgres container.
-- Invariants (spec §17): raw/ is append-only, crosswalk is append-only,
-- property_claim retains every competing value, person never reaches Neo4j.

CREATE TABLE IF NOT EXISTS fetch_manifest (
  id BIGSERIAL PRIMARY KEY,
  source_id TEXT NOT NULL, url TEXT NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  http_status INT, content_hash TEXT NOT NULL, raw_path TEXT NOT NULL,
  UNIQUE (source_id, content_hash)
);

CREATE TABLE IF NOT EXISTS raw_company (
  id BIGSERIAL PRIMARY KEY,
  source_id TEXT NOT NULL, source_key TEXT NOT NULL, raw_path TEXT NOT NULL,
  retrieved_at TIMESTAMPTZ NOT NULL, tier SMALLINT NOT NULL,
  legal_name TEXT, trade_name TEXT,
  cin TEXT, gstin TEXT, pan TEXT, udyam_no TEXT, iec TEXT,
  constitution TEXT,
  address_raw TEXT, pincode TEXT, city TEXT, state TEXT,
  phone TEXT, email TEXT, website TEXT,
  roles_claimed TEXT[],
  payload JSONB,
  -- Stage 2 outputs (filled by normalize.py, never overwrite legal_name)
  norm_name TEXT, district TEXT, cluster_id TEXT, geo_confidence TEXT,
  UNIQUE (source_id, source_key)
);
CREATE INDEX IF NOT EXISTS raw_company_norm_name ON raw_company (norm_name);
CREATE INDEX IF NOT EXISTS raw_company_pincode  ON raw_company (pincode);
CREATE INDEX IF NOT EXISTS raw_company_cin      ON raw_company (cin) WHERE cin IS NOT NULL;
CREATE INDEX IF NOT EXISTS raw_company_gstin    ON raw_company (gstin) WHERE gstin IS NOT NULL;

CREATE TABLE IF NOT EXISTS raw_facility (
  id BIGSERIAL PRIMARY KEY,
  source_id TEXT, source_key TEXT, raw_path TEXT,
  retrieved_at TIMESTAMPTZ, tier SMALLINT,
  company_source_key TEXT,
  firm_name TEXT,
  address_raw TEXT, pincode TEXT, state TEXT,
  licence_form TEXT, licence_no TEXT, licence_status TEXT,
  granted_on DATE, expires_on DATE,
  is_loan_licence BOOLEAN,
  loan_premises_name TEXT,
  categories TEXT[], payload JSONB,
  norm_name TEXT, district TEXT, cluster_id TEXT, geo_confidence TEXT,
  UNIQUE (source_id, source_key)
);
CREATE INDEX IF NOT EXISTS raw_facility_norm_name ON raw_facility (norm_name);
CREATE INDEX IF NOT EXISTS raw_facility_pincode   ON raw_facility (pincode);

CREATE TABLE IF NOT EXISTS raw_ingredient (
  id BIGSERIAL PRIMARY KEY,
  source_id TEXT, source_key TEXT, tier SMALLINT,
  inci_name TEXT, cas TEXT, ec_no TEXT,
  functions TEXT[], alias TEXT, payload JSONB,
  UNIQUE (source_id, source_key)
);
CREATE INDEX IF NOT EXISTS raw_ingredient_inci  ON raw_ingredient (inci_name);
CREATE INDEX IF NOT EXISTS raw_ingredient_alias ON raw_ingredient (alias);

-- Regulatory rules land here before Neo4j so the jurisdiction invariant is enforced in SQL too.
CREATE TABLE IF NOT EXISTS raw_rule (
  id BIGSERIAL PRIMARY KEY,
  source_id TEXT NOT NULL, source_key TEXT NOT NULL, tier SMALLINT NOT NULL,
  jurisdiction TEXT NOT NULL CHECK (jurisdiction IN ('EU','IN')),
  source_doc TEXT NOT NULL, clause TEXT,
  rule_scope TEXT NOT NULL CHECK (rule_scope IN ('ingredient','category')),
  subject TEXT NOT NULL,              -- inci_name or ProductCategory.code
  applies_to_group BOOLEAN DEFAULT FALSE,
  status TEXT,                        -- prohibited | restricted | colorant | preservative | uv-filter | standard
  max_concentration TEXT, conditions TEXT, standard_ref TEXT,
  payload JSONB,
  UNIQUE (source_id, source_key)
);
CREATE INDEX IF NOT EXISTS raw_rule_subject ON raw_rule (subject);

-- Observed products (S7). Never mints Company nodes.
CREATE TABLE IF NOT EXISTS raw_product (
  id BIGSERIAL PRIMARY KEY,
  source_id TEXT, source_key TEXT, tier SMALLINT,
  product_name TEXT, brands TEXT[], countries TEXT[],
  categories_tags TEXT[], ingredients_text TEXT, ingredients_tags TEXT[],
  payload JSONB,
  UNIQUE (source_id, source_key)
);

-- APPEND ONLY. Never UPDATE or DELETE.
CREATE TABLE IF NOT EXISTS crosswalk (
  id BIGSERIAL PRIMARY KEY,
  entity_type TEXT NOT NULL, source_id TEXT NOT NULL, source_key TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  method TEXT NOT NULL,   -- exact_cin | exact_gstin | exact_pan | exact_licence | fuzzy | manual
  score NUMERIC, decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  superseded_by BIGINT REFERENCES crosswalk(id)
);
CREATE INDEX IF NOT EXISTS crosswalk_live ON crosswalk (entity_type, source_id, source_key) WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS crosswalk_entity ON crosswalk (entity_id) WHERE superseded_by IS NULL;

CREATE TABLE IF NOT EXISTS review_queue (
  id BIGSERIAL PRIMARY KEY,
  entity_type TEXT, left_key TEXT, right_key TEXT,
  score NUMERIC, features JSONB,
  status TEXT DEFAULT 'pending',  -- pending | merge | distinct
  decided_by TEXT, decided_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS review_queue_pending ON review_queue (status) WHERE status = 'pending';

-- All competing values retained.
CREATE TABLE IF NOT EXISTS property_claim (
  id BIGSERIAL PRIMARY KEY,
  entity_id TEXT NOT NULL, property TEXT NOT NULL, value JSONB NOT NULL,
  source_id TEXT NOT NULL, tier SMALLINT NOT NULL,
  retrieved_at TIMESTAMPTZ NOT NULL, valid_from DATE, valid_to DATE
);
CREATE INDEX IF NOT EXISTS property_claim_ep ON property_claim (entity_id, property);

-- GSTIN verification results.
CREATE TABLE IF NOT EXISTS gst_record (
  id BIGSERIAL PRIMARY KEY,
  entity_id TEXT NOT NULL, gstin TEXT NOT NULL,
  legal_name TEXT, trade_name TEXT, constitution TEXT,
  status TEXT, taxpayer_type TEXT, registration_date DATE,
  address TEXT, pincode TEXT, nature_of_business TEXT[],
  last_gstr1_period TEXT, last_gstr3b_period TEXT,
  filing_streak_months INT,
  is_proprietorship BOOLEAN,     -- drives DPDP handling
  provider TEXT, verified_at TIMESTAMPTZ NOT NULL,
  UNIQUE (gstin, verified_at)
);

-- Negative signals.
CREATE TABLE IF NOT EXISTS adverse_signal (
  id BIGSERIAL PRIMARY KEY,
  entity_id TEXT, signal_type TEXT,  -- samadhaan_case | gst_cancelled | licence_suspended
  detail JSONB, source_id TEXT, observed_at TIMESTAMPTZ
);

-- People only here. Never in Neo4j.
CREATE TABLE IF NOT EXISTS person (
  id BIGSERIAL PRIMARY KEY,
  entity_id TEXT, name TEXT, role TEXT, email TEXT, phone TEXT,
  source_id TEXT, retrieved_at TIMESTAMPTZ,
  consent_basis TEXT, deleted_at TIMESTAMPTZ
);

-- Reference: pincode -> district -> state (India Post / GeoNames).
CREATE TABLE IF NOT EXISTS ref_pincode (
  pincode TEXT PRIMARY KEY,
  district TEXT, state TEXT, place TEXT
);

-- Trust levels, recomputed nightly (Stage 8). Never ingested.
CREATE TABLE IF NOT EXISTS trust_state (
  entity_id TEXT PRIMARY KEY,
  trust_level SMALLINT NOT NULL,
  reasons JSONB NOT NULL,          -- the human-readable "why", surfaced in the UI
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Ingredient strings that resolved to nothing. Never guessed (spec §7.4.4).
CREATE TABLE IF NOT EXISTS unresolved_ingredient (
  id BIGSERIAL PRIMARY KEY,
  raw_string TEXT NOT NULL, source_id TEXT, occurrences INT DEFAULT 1,
  best_candidate TEXT, best_score NUMERIC,
  first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (raw_string)
);
