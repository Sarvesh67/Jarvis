-- Trust evidence (TRUST_PLAN.md §6, §9 — phase T1, 2026-10-31).
--
-- One row per fact behind a firm's evidence grade and facets. DERIVED, like trust_state:
-- pipeline/trust.py rebuilds it each run from raw_company, operates_edge, adverse_signal
-- and (from T2) gst_record. The facts it is derived from are the append-only stores; this
-- table is their reading, so it is replaced, not appended to.
--
-- `visibility` is the display matrix of TRUST_PLAN §7.1. Only `public` rows reach Neo4j,
-- because the agent's Cypher can read anything there.

CREATE TABLE IF NOT EXISTS trust_evidence (
  id BIGSERIAL PRIMARY KEY,
  entity_id TEXT NOT NULL,
  facet TEXT NOT NULL CHECK (facet IN ('identity', 'licence', 'activity', 'track', 'adverse')),
  signal_type TEXT NOT NULL,
  value JSONB,
  source_id TEXT, source_key TEXT,
  publisher_family TEXT,            -- trust/families.py; NULL = never corroborates
  tier SMALLINT,
  provenance_class TEXT,            -- registry | directory | self | verification | first_party
  visibility TEXT NOT NULL DEFAULT 'public'
    CHECK (visibility IN ('public', 'private', 'consented', 'supplier_only')),
  public_text TEXT,                 -- the line a buyer reads; NULL unless visibility = public
  counts BOOLEAN NOT NULL DEFAULT true,   -- false: shown for context, never graded on
  observed_at DATE,
  expiry_at DATE,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS trust_evidence_entity ON trust_evidence (entity_id);
CREATE INDEX IF NOT EXISTS trust_evidence_facet ON trust_evidence (facet, signal_type);

-- The grade and facet states, beside the rung ladder they replace. trust_level and
-- trust_rungs stay for one release so nothing reading them breaks (TRUST_PLAN §6.6).
ALTER TABLE trust_state ADD COLUMN IF NOT EXISTS trust_grade TEXT;
ALTER TABLE trust_state ADD COLUMN IF NOT EXISTS facets JSONB;
