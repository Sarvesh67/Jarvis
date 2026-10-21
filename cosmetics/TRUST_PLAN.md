# Trust layer — plan for PRD Priority 2

Plan, not as-built. Covers PRD §6 (GST pipeline + trust score), the trust half of §8, and the
P2 metrics in §10.2. Written 2026-10-18 against the live stores. Every number below was
measured from them that day unless marked as a vendor list price.

Read `CLAUDE.md` and `catalog/glossary.yaml` first. This plan keeps every hard rule in them
and adds to them. It does not relax any.

---

## 0. Decisions in one screen

| # | Decision | Why |
|---|---|---|
| 1 | **No bulk verification. Verify lazily, at the moment of intent.** | Agrees with the brief. Cost then scales with the number of distinct suppliers surfaced, not with the registry. That is 10–80× cheaper than a sweep (§5). |
| 2 | **The real bottleneck is GSTIN *discovery*, not verification.** | We hold a GSTIN for **54 of 132,030** companies (0.04%). Verifying at the last minute only helps once we have a GSTIN to verify. Most of the effort goes into finding GSTINs, and that is lazy too (§3). |
| 3 | **Cards never wait on a vendor.** They render from cache with an "as of" date and upgrade while the user watches. Only an RFQ send blocks. | GSTN-backed calls take 0.3–3 s and slow down around the 20th–24th filing peaks. A spinner on every card would undo Priority 1. |
| 4 | **Two live vendors plus one cheap cross-check, behind one adapter**, normalised to GSTN's own public schema. | Gives backup and price leverage, and the cross-check measures vendor accuracy instead of assuming it (§4). |
| 5 | **Replace the 0–10 score with a four-part evidence profile.** | The PRD wireframe's "9.1 HIGH" next to "fulfilment evidence not yet available" breaks the PRD's own rule that unknown evidence stays unknown (§6). |
| 6 | **Fix L3. "GSTR-3B within 90 days" is wrong for most MSMEs.** | Quarterly (QRMP) filers fail it while fully compliant, and composition dealers never file 3B at all (§2.4). |
| 7 | **Filing history stays private. Suppliers can opt in to publish it, with consent.** | This turns the defamation-blocked signal into a sales asset for good suppliers (§8.2). |
| 8 | **Proprietorship GSTINs are masked in public. Buyers can match one without it being disclosed.** | The GSTIN contains the proprietor's personal PAN (§7, §8.10). |

**Phase 2.0 — done 2026-10-18 (₹0).** What it found and fixed:

- **13 personal PANs were readable on the demo.** 12 GSTINs on individual PANs (proprietorships)
  and 1 on an HUF. Each was exposed three ways: `trust_reasons` (shown as `why`),
  `Company.gstins`, and `Company.pan`, all readable by the agent's Cypher.
  `is_proprietorship` was never set because `gst_record` is empty. Now `pipeline/gstin.py`
  derives holder type from GSTIN char 6, the graph holds `24••••••••••1ZZ`, and `pan` is
  unset. The `no_personal_pan_in_graph` golden query found 13 before the reload and finds 0 after.
- **119,120 Udyam firms sat at L0 when they belong at L1.** `adapters/s17b_udyam_api.py` never
  set `udyam_registered`. It does now, and `--mark-registered` backfilled the landed rows.
  Rung-1 (Identity) evidence went from 4,445 companies (3.4%) to 121,460 (92.0%).
- **Rung-0 checks** run on every held GSTIN and land in `gstin_check`. Results: 0 checksum
  failures, 5 state mismatches out of 44 GSTINs with a resolvable state, 13 personal PANs.
- **`trust_state` kept 4,753 dead entities** from the 5 Sept re-resolution, because it was
  upsert-only. `bi.trust_distribution` counted them, giving 992 phantom L4s. `trust.py` now prunes them.
- **Live distribution after the fix:** L0 7,161 · L1 120,025 · L2 1 · L4 4,843.

---

## 1. What already exists (so the plan builds on it, not beside it)

| Piece | Where | State |
|---|---|---|
| Trust ladder L0–L6 with `trust_rungs`, reasons, demotion | `pipeline/trust.py`, `trust_state` | live. Distribution after Phase 2.0: L0 7,161 · L1 120,025 · L2 1 · L4 4,843. (The morning's figures were inflated by dead entities; see §0.) |
| `gst_record` (history, one row per check), `adverse_signal`, `person` | `sql/001_schema.sql` | schema only, 0 rows |
| S16 GSTIN verification | `catalog/sources/s16_gstin.yaml` | **blocked on a provider key** |
| GSTIN harvest from websites, checksum and name confirmation | `adapters/s14_company_sites.py`, decision 0010 | live. 58 candidates, 54 promoted |
| CIN on 1,662 entities (MCA partial) | S3 | blocked on a data.gov.in key |
| COS-8/9 licences, the only tier-1 Permission source | S2, `catalog/rti/0001` | RTI to Gujarat FDCA drafted, **not filed** |
| NABL labs (201 with cosmetics scope) | S11 | live |
| Corroboration: CHEMEXCIL, trade-fair exhibitors | S8, S9 → L4 | live |
| Claimed certifications and capabilities (tier 2) | S14 → `CLAIMS_*` | live, unverified by design |
| Planned adverse signals: `samadhaan_case`, `gst_cancelled`, `licence_suspended` | `adverse_signal` comment | not built |
| L5 Confirmed (supplier claims profile), L6 Vouched (buyer confirms a transaction) | `trust.py` docstring | not built |
| msme GST stub with a swappable `_call_api`, cached by GSTIN | `msme/gst.py` | dormant. Build the new verifier project-agnostic so msme can reuse it |
| Card UI with an L-score, "Why this score?" and a trust drawer | `serve/index.html` (uncommitted) | in progress (P1) |
| Marketing claims: "Entity verified", "TRUST High", "92/100", "2.1h response signal", "who-worked-with-whom graph" | `lattix-site/public/index.html` | **not yet backed by data.** See §7.6 |

PRD items this plan must satisfy (§6.3, §11.2): identity mapping without duplicates; a stored
verification event behind every badge; visible mismatch; history; "GST evidence
unavailable" as a fallback; a correction loop; a score that can be decomposed; GST never
implying fulfilment quality.

---

## 2. Questioning the premise

### 2.1 Is per-call verification where the money goes? No — discovery is.
54 GSTINs means a perfect last-minute verifier would light up 0.04% of cards. Every paid
verification has to be preceded by finding the GSTIN, and that step has its own costs and
false-match risk. §3 orders discovery cheapest first.

### 2.2 Should verification run "when answering"? Mostly yes, but not inline.
The answer should *trigger* verification without *waiting* for it. §4.2 has the trigger table.
A literal reading — verify every card before replying — adds a vendor round-trip to every
answer and pays for cards nobody opens.

### 2.3 Does the public API give us what we planned to use? Partly.
The public `searchTaxpayer` API (via a GSP) returns: `gstin, lgnm` (legal name), `tradeNam`,
`ctb` (constitution), `sts` (status), `rgdt`/`cxdt` (registration/cancellation date), `dty`
(taxpayer type — Regular/Composition/…), `nba` (nature of business), `pradr`/`adadr`
(principal/additional places, with pincode and lat/long), `stj`/`ctj` (jurisdiction),
`einvoiceStatus`, `lstupdt`. A separate public "track returns" API gives filing status and
frequency.

**Not available to us:** annual aggregate turnover slab, % of tax paid in cash, the GST
compliance rating, Aadhaar-authentication status and gross taxable income. The GST portal
shows these only **after login**, so no public API returns them. Don't design a signal around them.

**Free bonus:** `einvoiceStatus = Yes` means GSTN's records show aggregate turnover of at least
₹5 Cr in some year since 2017-18. E-invoicing has been enabled at ₹5 Cr since 1 Aug 2023,
counted at PAN level. It's a public, legal lower bound on scale, returned by the same call. GSTN
says enablement does not create an obligation, so word it as "e-invoicing enabled", never "large company".

### 2.4 Is the L3 rule right? No.
`trust.py` awards L3 for "GSTIN active and GSTR-3B filed within 90 days".
- **QRMP filers** (turnover up to ₹5 Cr, the bulk of this industry) file 3B **quarterly**, by
  the 22nd/24th of the month after the quarter. On 21 Oct, a fully compliant QRMP filer's
  latest 3B is for April–June: 113 days old. It fails.
- **Composition dealers** (`dty = Composition`) file CMP-08 quarterly and GSTR-4 annually,
  and **never file 3B**. They fail permanently.
- Worse, the ladder labels are visible in the UI. "GST active" without the "Operating" rung
  invites the reader to infer "not filing". That is **defamation by implication**, and for
  these two groups it is also false.

**Fix:** compute "filing on schedule" against the taxpayer's own frequency and return types
(`dty` plus track-returns). Make it a **private** evidence item. The public Activity facet
must never encode filing (§6).

### 2.5 Does "GST verified" mean anything yet? Define it once.
> **GST verified** = GSTN reported this GSTIN as *Active*, on a check made through a
> GSP-backed provider within the last 30 days, and the legal or trade name matches our record.

Two corollaries. A response from a vendor's own cached copy is "on record", not "verified"
(§4.1). After 30 days the badge decays to "Verified on <date>". The PRD asks for recency.

### 2.6 Smaller things the build already gets wrong
- **Ranking by `trust_level DESC`.** Five templates do this. It contradicts `trust.py`'s own
  note that the ladder is not a quality order: an L4 council member without a licence
  outranks an L2 licensee. Rank on fit first, then on the evidence facet that matters for the question.
- **State codes vs addresses.** 5 of the 44 GSTINs with a resolvable state carry a state code
  that disagrees with every state on the record (e.g. `08…` Rajasthan against a Delhi pincode).
  Either it's a head-office GSTIN on a branch record, or it's a wrong merge. A free check
  catches this before any money is spent (§3, step 0).
- **The agent and money.** If the agent can call a paid verifier, one retry loop becomes an
  invoice. The agent gets cached evidence only. Paid calls go through the budgeted API, triggered by user actions.

---

## 3. GSTIN discovery — cheapest first, confirm before linking

Each rung runs only if the previous one produced nothing. All of them run lazily (on
surfacing or intent), except rung 2, which is a one-off batch because it is small and cheap.

| Rung | Method | Cost | Yield / scope | Link rule |
|---|---|---|---|---|
| 0 | **Deterministic checks on any GSTIN we hold**: checksum; state code (chars 1–2) vs our pincode's state; PAN type (char 6: `C` company, `F` firm/LLP, `P` individual → proprietorship, `H` HUF, `T` trust…) vs the constitution we hold; entity number (char 13) > 1 means several registrations in that state | ₹0 | every candidate | **built**: `pipeline/gstin.py` → `gstin_check`. A failure blocks the paid call (`review_queue` holds pairs, so it doesn't fit there) |
| 1 | **Harvest** from pages we already crawl (S14), exhibitor and council pages, and anything a user uploads | ₹0 | low; 58 so far | decision 0010: the company's own name must appear on the page |
| 2 | **CIN → PAN → GSTINs** for the 1,662 entities with a CIN | ~₹1 + ₹1 per entity (Infyner list price) → **≈ ₹3.3k once** | companies only | keep the GSTIN whose state matches our record; record the others as additional registrations |
| 3 | **Name + state → GSTIN search** (Attestr, Surepass, Meon offer it), on detail-open of an unidentified supplier | quote needed; cap 300/month at first | the long tail | auto-link **only if** the normalised legal *or* trade name matches **and** the `pradr` pincode/district matches. Anything else → `review_queue` as `name_search_candidate`. Attestr searches legal names only, so trade-name-only firms will miss |
| 4 | **Supplier claims the profile** (L5) and enters its GSTIN | ₹0 + one verification | grows with onboarding | GSTIN plus a domain or bank-name proof (§8.6) |
| 5 | **Buyer contributes**: pastes a supplier's GSTIN ("free trust check", PRD §7.2), or uploads an invoice or e-invoice QR | ₹0 + one verification | grows with usage | the buyer's consent is recorded. The GSTIN links only via rung 0 plus a name/pincode match |

**Identity model.** One PAN can have many GSTINs: one or more per state. The *company* is PAN-level
and the *registration* is GSTIN-level. `adadr` lists additional places, which is how a
plant in Baddi and a head office in Mumbai turn out to be one firm. Every link is a
`crosswalk` row (append-only) with `method ∈ {exact_gstin, cin_pan_bridge,
name_search_confirmed, supplier_claim, buyer_contributed}` and a confidence. A GSTIN
already linked elsewhere is never silently re-pointed: it goes to review. That is how the PRD's
"no duplicate company records" holds.

---

## 4. Verification — vendors, triggers, freshness, money

### 4.1 Vendors

List prices seen 2026-10-18, before 18% GST. Confirm before buying. The column that matters
most is **source**: "live" means the provider called GSTN for this request. "Cache" means it
answered from its own copy, which can be weeks old.

| Provider | Type | Published price / call | Source | Offers | Proposed role |
|---|---|---|---|---|---|
| **Infyner** | aggregator | ₹1.80–3.00 *official verify*; ₹0.60–1.00 own-data lookup; CIN→PAN ₹0.60–1; PAN→GSTIN ₹0.60–1 | both, priced separately | verify, PAN→GSTIN, CIN→PAN, MCA company search (500/day free) | **discovery (rung 2)**; secondary live |
| **gstinapi.com** | API reseller | ₹0.50 → ₹0.40 above 2,500 credits; failed checksums free; 20 free credits | not stated | verify, filing, e-invoice | cheap tier. Promote only after its source is proven live |
| **GSTZen** | GST software (has a GSP arm) | ₹0.33–0.70 (annual packs: 5k/10k/25k) | not stated | validate + details | cheap tier / cross-check |
| **Eko** | fintech APIs | ₹0.72, per successful call | not stated | verify | alternative cheap tier |
| **GSTVerify** | reseller | ₹0.08–0.10; ₹5k/month unlimited | likely cache | verify | cross-check only, until its sourcing is known |
| **Quicko/Sandbox, Masters India, Vayana, IRIS, Cygnet** | **GSPs** (GSTN-licensed) | quote needed | live | public search + track returns; **consented taxpayer APIs** (GSTR-1/3B/2B, e-invoice) | **primary live**, and the only route for consent features (§8.2, §8.4) |
| **Cashfree Secure ID, Surepass, Gridlines, Signzy, HyperVerge** | KYB aggregators | dashboard / quote; free trial credits (Cashfree) | live via GSPs | verify, PAN→GSTIN, name→GSTIN (Surepass), bank penny-drop | secondary live; bank verification for L5 |

**Due diligence before any vendor gets traffic.** These answers go in its source card:
1. Are you a GSP, or which GSP do you call? Written evidence.
2. Is a response live or cached? Does it carry a fetch timestamp? (If not, we treat it as cache.)
3. Do you ever query the captcha-protected portal? **A yes disqualifies the vendor**: it is a
   ToS and unauthorised-access exposure that we would inherit.
4. Will you sign a Data Processing Agreement (DPDP s.8(2))? List sub-processors, data
   location and deletion terms.
5. May we store the response and show its facts to our users? For how long?
6. Our use case is *B2B supplier diligence on behalf of buyers*, about firms who are not our
   customers. Is that allowed under your terms? (Attestr's docs require the client to obtain
   consent for personal data. Get the answer in writing.)
7. Billing: are failures, invalid GSTINs and timeouts charged? Idempotency keys?
8. Rate limits, SLA, status page; which fields you return (`einvoiceStatus`, `adadr`, track-returns).

**Practicalities.** Every one of these signs a *business*. Lattix needs its own legal entity and
GSTIN for vendor KYC, invoices and input-tax credit on the 18%. Sarvesh signs up, because
account creation cannot be automated from here. Use free trial credits (gstinapi.com 20,
Cashfree's trial, Infyner's sandbox key) to build the adapter before paying anyone.

### 4.2 When verification runs

| Moment | Action | Blocks the user? |
|---|---|---|
| Answer renders cards | read cache; queue an async check for up to **3** visible cards whose status is older than 30 days | **no** — badge upgrades in place |
| Detail open, "Why this score?" | check if older than 7 days | no — spinner on that one chip, wait ≤ 2.5 s, then "checking…" |
| Shortlist, compare | check if older than 7 days | no |
| Contact click | ensure younger than 30 days | no |
| **RFQ send** | ensure younger than 24 h. If not Active, warn the buyer **privately** before sending | **yes**, ≤ 10 s, then fall back to the secondary vendor, then "could not verify right now — send anyway?" |
| "Show only GST verified" (PRD §8.1) | serve the fresh-verified set; *verify until filled*, up to a cap; say "N more not yet checked" | partially |
| Supplier claims profile | verify immediately with the live primary | yes |
| **Proprietorship (PAN type `P`)** | **never on render** — only on an explicit buyer action (detail open or later). Purpose-bound processing of personal data | — |

**Single-flight.** Concurrent requests for one GSTIN share one call (Postgres advisory lock
plus an in-process future). Otherwise two tabs pay twice.

### 4.3 Freshness, per field

| Field | Display TTL | Why |
|---|---|---|
| `sts` (status) | 30 d for the card; 7 d on intent; 24 h before an RFQ | auto-suspension for non-filing (Rule 21A) can happen in any month |
| names, `ctb`, `rgdt`, `dty` | 180 d | rarely change; `lstupdt` reveals a change |
| `pradr`, `adadr` | 90 d | places get added |
| filing status (private) | refresh after each due date (20th / 22nd / 24th) | the only time it can change |
| `einvoiceStatus` | 180 d | set annually |
| Negative: invalid / not found | 30 d | stops repeated paid misses |

Stale-while-revalidate everywhere. Past its TTL a fact is still shown, labelled with its
date, and never as "verified".

### 4.4 Router

Ordered providers per *purpose*. High-stakes purposes (RFQ, claim) go to the live primary,
then the live secondary. Render and intent go to the cheapest provider that is proven live. A
circuit breaker trips on error rate or p95 latency. A hedged request fires to the next
provider when the first passes its p95. A **2% shadow sample** goes to two providers, and the
disagreement rate per field, per vendor, is a published metric. A vendor above threshold is
demoted automatically. Every response is landed in `raw/s16_gstin/` (content-addressed,
append-only, as for every other source) and normalised to the GSTN field names above.

**Budget guard.** A `verification_spend` ledger with a monthly cap (start at ₹5k), a daily cap,
and a per-IP limit on the public "verify" and "free check" actions. When a cap is hit, the
system serves cached facts with their dates. It never fabricates, and it says "verification
paused for today" where it applies. This mirrors the demo's existing "come back tomorrow"
budget.

---

## 5. Cost — why lazy wins, in numbers

Lazy cost is bounded by **distinct suppliers surfaced**, not by traffic:

> calls / month ≈ U_render × (30 / TTL_render) + U_intent × (30 / TTL_intent) + RFQs

Worked example: 2,000 distinct GSTIN-bearing suppliers surfaced a month, 25% of them
opened or shortlisted, 50 RFQs. That gives 2,000 + 500×4 + 50 ≈ **4,050 calls/month at most**.

| Price / call | Lazy (≤ 4,050/mo) | One bulk sweep of 132,030 | Bulk + monthly status refresh |
|---|---|---|---|
| ₹0.40 (cheap tier) | **₹1.6k / mo** | ₹52.8k | ₹52.8k / mo |
| ₹1.80 (live, volume) | **₹7.3k / mo** | ₹238k | ₹238k / mo |
| ₹3.00 (live, list) | **₹12.2k / mo** | ₹396k | ₹396k / mo |

Bulk is not even possible today, since we lack the GSTINs. One-off discovery costs: rung 2
≈ ₹3.3k (inside Infyner's ₹10k minimum pack, whose credits last 365 days and work across its
APIs); rung 3 is capped at 300 searches/month until its false-match rate is measured. **Start
budget: ₹10k one-off + a ₹5k/month cap.** Revisit when U passes 5,000.

---

## 6. From one score to an evidence profile

The PRD's wireframe shows "9.1 / 10 HIGH" above "Fulfilment evidence not yet available", and
the site shows "TRUST High 92/100". A single number has to turn "unknown" into *something*,
and the PRD forbids exactly that (§6.4: "unknown evidence remains unknown"). Show four facets,
each with a state of `verified · claimed · unknown · adverse`:

| Facet | Question | Evidence (tier) | Current rungs |
|---|---|---|---|
| **Identity** | Does it legally exist, and is it this firm? | GSTIN Active + name match (1), CIN (1), Udyam (1), checksum only (3) | L1 |
| **Permission** | May it make *this* category? | COS-8/9 with category endorsement (1, via RTI); NABL scope for labs (1); claimed licence (2) | L2 |
| **Activity** | Is it operating now? | e-invoicing enabled (1); exhibited this year (2); live website (3); *filing on schedule — private only* | L3 (redefined), L4 |
| **Track record** | Have buyers dealt with it, and how did that go? | e-invoice-verified transactions (1, §8.1); RFQ response time measured by us (§8.7); buyer outcome (2) | L5, L6 |

**Card line:** `Identity ✓ · Licence ? · Active ✓ · Track record —` with "GST active · checked 2 d ago".
**Headline label**, by rule: *Well evidenced* (Identity and Permission verified, plus one
more), *Partly evidenced*, or *Thinly evidenced*. The label describes **our evidence, never the
supplier**. If a number is wanted, use "3 of 4 facets evidenced", a count with no claim of quality.

`trust_level`/`trust_rungs` stay as the storage and filter layer (`2 IN trust_rungs`). Facets
are computed from rungs plus the new `trust_evidence` rows, so nothing downstream breaks.
Ranking stops using `trust_level DESC` (§2.6). Public demotion requires a **tier-1** adverse
fact. Lower-tier adverse signals are private warnings at most.

---

## 7. What is shown, to whom, and on what legal basis

### 7.1 Display matrix

| Fact | Public card (company) | Public card (proprietorship) | Signed-in buyer (private) | Supplier (own profile) |
|---|---|---|---|---|
| GST status | "Active, checked <date>" / "Could not be confirmed active, checked <date>" | same | exact status (Cancelled / Suspended) and its date | exact |
| GSTIN | full | **masked** `••••••••••1Z5`, with a match check (§8.10) | masked + match check | full |
| Legal name | shown | **hidden** (it is the proprietor's own name) | hidden | shown |
| Trade name, constitution, registration date, nature of business | shown | shown, except the legal name | shown | shown |
| Address | principal place, district level on the card | district only | full principal place | full |
| Name mismatch | "GST record name differs from ours" + both names | note only, no names | both | both, and a "fix it" action |
| e-invoicing enabled | "e-invoicing enabled" | **not shown** (personal financial signal) | shown | shown |
| Filing on schedule | **never** | **never** | "Recent returns appear filed on schedule" / "We could not confirm recent filings — ask the supplier" | shown, with the periods |
| Filing periods and gaps | never | never | never | shown |
| Consented compliance badge (§8.2) | shown if the supplier opted in | shown if opted in | shown | managed here |
| Tier-1 adverse (cancelled licence, cancelled GST) | factual line + source + supplier's response | same | same + detail | same + respond |
| Track record | count, with ≥ 2 distinct buyers; buyer names only if they opted in | same | same | which buyers, if they opted in |

**Rule for the agent:** the public demo agent's context gets **public fields only**,
enforced at the API (data minimisation), not by prompt. It may quote evidence states
verbatim and may not describe a supplier ("unreliable", "risky", "defaulter"). Add a golden
check: no private field ever appears on a Neo4j `Company` node (the agent's Cypher can read
anything in Neo4j), mirroring `no_person_nodes`.

### 7.2 Acquisition is lawful only through sanctioned channels
- **GST data only through GSP-backed APIs**, the channel GSTN licenses for this. Never scrape the
  captcha-protected portal, never use a captcha-solving vendor (due-diligence question 3).
- **data.gov.in (Udyam, MCA)** is released under the Government Open Data License – India:
  reuse is allowed **with attribution**. The evidence drawer shows "Source: Udyam (Ministry of
  MSME) via data.gov.in", which the PRD wants anyway.
- **State FDA registers through RTI**, the statutory route. The Gujarat draft already pre-empts
  the s.7(9) and s.8(1)(j) refusals. Repeat it per state (§8.5).
- **Issuer registries** (IAF CertSearch, NABL, certification bodies) through their own public
  lookup or API, within their terms, at low volume.

### 7.3 Personal data — DPDP Act 2023 + DPDP Rules 2025
The Rules were notified in November 2025. The Data Protection Board provisions are in force,
and **the main obligations apply from about May 2027** (notice, consent, security, breach
reporting). Building to them now costs little. Retrofitting later costs a migration.
- **What counts as personal:** a proprietor's GSTIN (it embeds their PAN), their legal name,
  a residential-looking address, phone and email; also NABL lab contacts (already in `person`).
- **Don't rely on the public-data exemption** (s.3(c)(ii)). Commentators call its scope
  unclear, and whether GSTN's search page is publication "under a legal obligation" is
  untested. Design as if the Act applies: **minimise** (mask, hide the legal name), **bind to a purpose**
  (verify proprietorships only on a buyer's action, §4.2), **honour erasure** (`person`-table
  handling, `deleted_at`), publish a **grievance contact**, and set retention (purge
  proprietorship GST payloads 12 months after last surfacing unless the supplier claimed the profile).
- **Consent** wherever a person gives us data: supplier claim, buyer upload, the free check.
  Use one `consent_record` table with scope, purpose, granted/revoked timestamps and artifact.
- **Bank details** from a penny-drop at claim time count as *sensitive personal data* under
  the IT Act's SPDI Rules 2011, which apply today. Store only the result (name match yes/no),
  never the account number.
- **Vendors are Data Processors**, so a DPA with each one (question 4).

### 7.4 Defamation (BNS s.356 and civil)
Defences are truth for the public good and good faith. Design for both:
- facts only, each with its source and check date; no adjectives;
- negative regulatory facts are shown only at tier 1, alongside a **supplier response** field;
- "Report incorrect info" (PRD §6.3): acknowledge within 24 h; a disputed public fact gets a
  "disputed by supplier" tag within 72 h and a decision within 15 days. These are the IT
  Rules 2021 grievance timelines, adopted voluntarily. Lattix writes its own profiles, so
  intermediary safe harbour (IT Act s.79) probably does not protect it, and it should behave as a careful publisher;
- the agent cannot characterise (§7.1).

### 7.5 Credit-information boundary (CICRA 2005)
Running a "credit information" business needs RBI registration. The Act ties credit
information to borrowers of credit institutions: loans, security, guarantees,
creditworthiness. Stay outside it:
- hold **no** loan, repayment, MCA-charge or bureau data; never call the output a "credit score" or "rating";
- **MSME Samadhaan** cases where the supplier is the *respondent* are payment-default facts,
  which is credit-like. Leave them out of v1. If added later, add them only after legal review, private, and factual;
- the Account Aggregator route to GST returns (GSTN has been an FIP since 2023) is open only
  to regulated FIUs. Lattix is not one. Use GSP consent APIs instead (§8.2).

### 7.6 Claims Lattix makes about itself
The landing page shows "Entity verified", "TRUST High", "92/100", "2.1h response signal",
"who-worked-with-whom graph". Either back each one with the definitions here (§2.5, §6, §8.1,
§8.7) or label the mock as illustrative. Never say "Government verified" or imply GSTN
endorsement (the Emblems and Names Act). Write "Checked against the GST Network registry via <provider>".

### 7.7 Terms to draft (lawyer review)
- **Buyer terms:** trust evidence is informational, not a guarantee; buyers do their own
  diligence; liability is limited. This matches the PRD non-goal "trust score not proof of quality".
- **Supplier terms:** accuracy warranty; licence to display; the scope and duration of any
  GST-data consent; revocation; the right to respond.
- **Privacy notice** (DPDP s.5): what we process about suppliers and buyers, why, how to erase.
- **Upload terms:** the buyer confirms they may share the invoice. We extract the minimum and discard the rest (§8.1).

---

## 8. Ten things that make this better than a GST badge (all lawful)

1. **E-invoice signed QR as cryptographic vouching (L6, no self-reporting).** Every
   e-invoice carries a QR signed by the Invoice Registration Portal (JWS, SHA256-RSA): IRN,
   seller GSTIN, buyer GSTIN, date, value. The IRPs are NIC 1/2, Cygnet, ClearTax, E&Y and IRIS. A
   buyer scans or uploads one; we verify the signature **offline** against the IRP's public key
   (₹0), proving a GST-registered B2B sale between the two firms. This is the
   "who-worked-with-whom graph" made real. Covers suppliers at ₹5 Cr+, which describes the
   contract manufacturers buyers most want. Anti-gaming: reject buyer-seller pairs on the same PAN. Store the value band only.
2. **Consent-to-publish compliance.** A supplier grants OTP consent through a GSP. We pull its own
   GSTR-1/3B status and show "Returns filed on time, last 12 months — verified with the
   supplier's consent, <date>". The signal we must never publish *about* a supplier becomes one
   the supplier publishes *about itself*. It's revocable by the supplier on GSTN's consent dashboard (live since Nov 2025).
3. **The GSTIN string is free evidence.** Checksum, state, PAN type → constitution, and
   multiple registrations are all checked before money is spent (§3, step 0). It already
   caught 5 state mismatches and 13 personal PANs (12 proprietorships, 1 HUF) in 54 GSTINs.
4. **Buyer-consented GSTR-2B as relationship evidence.** A buyer's own 2B lists the suppliers
   who actually invoiced them. With OTP consent we learn which surfaced suppliers a brand
   genuinely uses. That evidence works below the ₹5 Cr e-invoice line. Keep the supplier GSTIN
   and a months-active band; drop invoice values.
5. **RTI as a pipeline, not a one-off.** The Gujarat COS-8/9 draft becomes a template per
   state FDA: Maharashtra, Tamil Nadu, Himachal and Delhi report zero on ONDLS and hold most of
   the industry. Add a second request per state for *cancelled or suspended* cosmetics
   licences, which is tier-1 adverse evidence. ₹10 and 30 days each, with a first-appeal
   calendar. It is the only route to a tier-1 `PERMITS` edge (decision 0012).
6. **Anti-impersonation at claim.** Before L5, require a GSTIN, an email on the website's
   domain or a DNS TXT record, and optionally a penny-drop where the bank's account-holder
   name matches the GST legal name. Each costs ₹0–3. Together they stop someone claiming a
   competitor's profile.
7. **Response time measured on our own RFQ rail.** It is our observation, not their claim.
   Show the median with n (minimum 3). The supplier sees its own figure. This is what can back
   the site's "2.1h response signal".
8. **Certificates verified at the issuer.** Upgrade `CLAIMS_CERTIFICATION` (tier 2) to
   verified where the issuer has a registry: IAF CertSearch for ISO (API since April 2025, 3.2M
   accredited certificates), NABL (held), COSMOS/Ecocert, Leaping Bunny/PETA lists. A
   website claim that **fails** at the issuer stays private and goes to review. It is never shown as "fake".
9. **Status-change alerts to shortlisters.** `gst_record` already keeps history. A move from Active
   to Suspended or Cancelled privately notifies buyers who shortlisted that supplier. It's
   lawful (private, factual, a legitimate interest), it's the retention hook for PRD
   "Progress lock", and it's the weekly digest's first useful line.
10. **Match without disclosure.** The proprietorship GSTIN stays masked, but a buyer can type
    the GSTIN their supplier gave them and get "matches this profile" or "does not match". The
    comparison happens on the server and reveals nothing new. It's the PRD's "free trust check"
    for exactly the firms we can't publish, and every check is consented discovery input (§3, rung 5).

---

## 9. Data model and code changes

**Postgres** (`sql/003_trust.sql`, additive):
- `gst_record` (exists, append-only): add `provider_class` (`live_gstn` | `vendor_cache`),
  `raw_sha256`, `einvoice_enabled`, `taxpayer_type` (from `dty`), `filing_frequency`,
  `trigger` (render/detail/shortlist/rfq/claim/free_check), `cost_inr`, `latency_ms`.
- `trust_evidence` (the PRD §9.1 entity): `entity_id, facet, signal_type, value jsonb,
  source_id, tier, provenance_class, visibility (public|private|consented|supplier_only),
  observed_at, expiry_at, confidence, superseded_by`.
- `verification_spend` (ledger, used by the budget guard; reconciled monthly against vendor invoices).
- `trust_feedback` (PRD correction loop): field, claim, reporter, status, resolution, timestamps.
- `supplier_response` (right of reply), `consent_record`.
- Proprietorship GSTINs live with `person`-grade handling: erasable, consent basis recorded.

**Code** (`cosmetics/trust/`, with no cosmetics-specific imports so `msme/gst.py` can be replaced by it):
- `discovery.py` — the rungs in §3 and the link rules; it writes to `crosswalk`/`review_queue`.
- `gst/adapter.py` (interface + normaliser), `gst/providers/*.py`, `gst/router.py`
  (breaker, hedging, shadow sampling), `gst/cache.py` (field TTLs, single-flight).
- `budget.py` — caps and the ledger.
- `facets.py` — the evidence profile. `pipeline/trust.py` keeps the rungs and calls it. L3 is redefined (§2.4).
- `qr.py` — e-invoice JWS verification against IRP public keys fetched from the official
  portal and pinned (never hard-coded from a third-party page).
- API (PRD §9.2): `GET /vendors/{id}/trust` (public fields only), `POST /vendors/{id}/verify`
  (budgeted, rate-limited), `POST /vendors/{id}/report`, `POST /trust/check-gstin` (match without disclosure).
- `skills/jarvis-cosmetics-search`: the agent reads `/trust` and quotes states; it gets no paid-call tool.
- Catalog: S16 moves from `blocked` to `live` with vendor cards; new cards for IAF and e-invoice QR. `make catalog-check` covers them.
- Golden queries: `no_private_trust_fields_in_neo4j`, `no_unmasked_proprietorship_gstin_public`,
  `every_gst_badge_has_a_record` (PRD §11.2).

---

## 10. Delivery

| Phase | Scope | Cost | Exit criteria |
|---|---|---|---|
| **2.0 — done 2026-10-18** | masked personal-PAN GSTINs in `trust_reasons`, `gstins` and `pan`; set `udyam_registered` in s17b; rung-0 checks; pruned dead trust rows; skill and schema-card rules | ₹0 | **met:** 0 personal PANs in the graph (golden query); `1 IN trust_rungs` for 92.0%; mismatches in `gstin_check`. Still open: the "GST verified" definition, and the P1 card's "Entity verified" chip (`serve/index.html:886`). It fires on rung 1, which now covers 92% of firms on Udyam membership alone. Suggest "Registered (Udyam)" / "GSTIN on record" until a live check backs "verified" |
| **2.1 — week 1–2** | adapter + 2 providers on trial credits; cache, TTLs, single-flight; budget guard; `gst_record` writes; lazy triggers; badge and drawer with "checked <date>" | trial credits | every badge traces to a `gst_record` row; render p95 unchanged; spend ledger matches the vendor console |
| **2.2 — week 2–3** | rung 2 (CIN→PAN→GSTIN batch); rung 3 on detail-open with confirmation; the free GSTIN check (match without disclosure) | ≈ ₹10k pack + ₹5k/mo cap | GST coverage of *surfaced* vendors ≥ 30%; name-search false links < 2% on a 100-row audit |
| **2.3 — week 3–4** | facet UI replacing the L-score; ranking fix; L3 redefinition; feedback + supplier response; privacy notice and terms drafted for review | ₹0 + lawyer | PRD §11.2 acceptance criteria all pass; the score breaks down into facets |
| **2.4 — with real users** | supplier claim (L5) with domain/bank proof; consent compliance badge; e-invoice QR vouching (L6); IAF certificate checks; status alerts; RTIs to 4 more states | per use | ≥ 1 supplier with a consented compliance badge; ≥ 1 QR-verified transaction; RTI replies logged |

**Metrics** (PRD §10.2, plus what the design needs): GST coverage of surfaced vendors; match
success by discovery method; evidence-drawer open rate; correction rate; contact after trust
view; **cost per verified card**; cache hit rate; vendor disagreement rate; false-link rate
from name search; time to badge.

---

## 11. Decisions only Sarvesh can make

1. **Vendor accounts.** Sign up for trials with gstinapi.com, Cashfree and Infyner, and ask
   two GSPs (Quicko/Sandbox, Masters India) for quotes using the §4.1 questionnaire. This needs Lattix's entity and GSTIN.
2. **Budget:** ₹10k one-off + ₹5k/month cap to start?
3. **Drop the 0–10 score** for the facet profile (§6), including on the landing page?
4. **Public wording for non-active GST:** "Could not be confirmed active, checked <date>" (proposed), or exact status?
5. **Lawyer review:** DPDP basis for processing proprietorships; the display matrix; the CICRA
   boundary; buyer and supplier terms; the Samadhaan question.
6. **File RTI 0001** (Gujarat), as only an Indian citizen can, and approve the per-state template.

## 12. Risks

| Risk | Mitigation |
|---|---|
| A vendor's cached data is wrong or stale | `provider_class`; live-only for "verified"; shadow cross-check; automatic demotion |
| GSTN slows down around filing dates | stale-while-revalidate; nothing on the render path waits |
| Name search links the wrong firm | confirmation rule; review queue; audited false-link metric; cap |
| The public demo drives spend | budget guard; per-IP caps; the agent has no paid tool |
| Coverage stays low | honest "GST evidence unavailable" (PRD fallback); discovery prioritised by how often a firm surfaces |
| Defamation or DPDP exposure | the display matrix, tier-1-only adverse, right of reply, masking, purpose binding, golden checks |
| Vendor terms forbid third-party diligence | ask in writing first (question 6); keep two vendors |

---

## Sources (consulted 2026-10-18)

- GST portal, Search Taxpayer manual (which fields are public and which need login): https://tutorial.gst.gov.in/userguide/taxpayersdashboard/Search_Taxpayer_manual.htm
- searchTaxpayer response schema (Vayana GSP docs): https://docs.gsp.vayana.com/GSTN/searchTaxpayer-get-api/
- GSTN consent dashboard for ASP access, Nov 2025: https://octagst.com/blog/govt-gst-portal-check-api-access
- Quicko GSP public and taxpayer APIs: https://help.quicko.com/portal/en/kb/articles/gst-apis-available-via-quicko-gsp
- Prices: https://www.infyner.com/api-pricing · https://gstinapi.com/pricing · https://gstzen.in/gst-validator-api-pricing-details · https://eps.eko.in/products/gst-verification-api · https://gst.getbusiness.co.in/pricing/ · https://www.cashfree.com/gst-verification/
- Name → GSTIN: https://attestr.developerhub.io/attestr-docs/search-gstin-by-company-name-api · https://surepass.io/gst-number-search-by-pan-api/
- E-invoice enablement at ₹5 Cr: https://www.taxscan.in/?p=289129 · signed QR: https://www.mastersindia.co/gst/signed-qr-code-e-invoicing-system/ · private IRPs: https://www.mahagst.gov.in/public/uploads/whatisnew/1761741007GSTN%20launches%20e-invoice%20registration%20services%20with%20private%20IRP%E2%80%99s.pdf
- QRMP due dates: https://tax2win.in/guide/qrmp-scheme-under-gst
- DPDP Rules 2025 phasing: https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/nov/doc20251117695301.pdf · https://dsklegal.com/?p=26455
- DPDP s.3(c)(ii) scope: https://law.asia/publicly-available-data-dpdpa/ · https://www.india-briefing.com/news/india-dpdp-act-publicly-available-personal-data-46899.html/
- GSTN as an AA FIP: https://setu.co/blog/the-gst-data-tale-on-account-aggregator
- CICRA 2005: https://www.ifsca.gov.in/Document/Legal/71-the-credit-information-companies-regulation-act-2005-12092020034712.pdf
- IAF CertSearch API: https://iaf.news/2025/09/30/iaf-certsearch-api-real-time-verification/
