# Trust layer — plan for PRD Priority 2

Plan, not as-built. Covers PRD §6 (GST pipeline + trust score), the trust half of §8, and the
P2 metrics in §10.2. First written 2026-10-18. **Revised 2026-10-31**: §0, §1, §6, §9, §10
and §11 are new, and §2.7–§2.8 were added. The vendor, legal and ideas sections (§3–§5,
§7, §8) are carried over unchanged. Every number was measured from the live stores on the
date given, unless marked as a vendor list price.

Read `CLAUDE.md` and `catalog/glossary.yaml` first. This plan keeps every hard rule in them
and adds to them. It does not relax any.

---

## 0. Decisions in one screen

| # | Decision | Why |
|---|---|---|
| 1 | **Retire the L0–L6 number. Show one evidence grade plus three facets** (§6). | The ladder is not an order. 3,408 of the 4,843 L4 firms have no identity evidence at all. L2 is per category. L3 cannot be reached. "Highest rung reached" skips all three (§2.8). |
| 2 | **Grade order: GST verified, then corroborated by 3+ independent publishers, then by 2, then registered, then listed.** A tier-1 adverse fact overrides the grade. | Owner's call, 2026-10-31: GST verification at the top, and everything below it decided by how many independent sources agree. |
| 3 | **Independence is counted by publisher, not by source id.** One registry counts once. A firm's own website never corroborates. Nothing corroborates without identity. | S17 and S17b are both Udyam, and that double count produced 1,090 L4s. S14 crawled sites it found through CHEMEXCIL's links, which produced 1,266 more. |
| 4 | **Licence, Activity and Track record are facets beside the grade. They are never folded into it.** | A licence covers one category. Activity decays with time. Track record is first-party. A single number hides all three. |
| 5 | **Trust never sorts results.** | Eight templates use `trust DESC`, so the 3.7% of firms at L4 fill the top of every list. Rank by fit first (§6.5). |
| 6 | **Use the tier-1 adverse facts we already hold.** These are MCA status Struck off, Dissolved or Liquidated (683 firms), and NABL certificates past their validity date (258). | Today 826 firms read "Legally exists — CIN …" while MCA lists the company as not active. |
| 7 | **Build GST behind a stub first. A stub record can never produce a verified badge.** | This exercises the cache, triggers, budget and drawer before any vendor is chosen. The msme stub's "any 15 characters = verified" must not be copied. |
| 8 | **GSTIN discovery is the bottleneck, and GST is a gate at RFQ time, not at discovery.** | We hold 54 GSTINs. Udyam registration already answers "is it real" for 92% of firms. GST adds "is it still active", which matters most when money is about to move (§2.7). |
| 9 | **No bulk verification. Verify lazily, at the moment of intent, and cards never wait on a vendor.** | Carried over from 2026-10-18 (§4.2, §5). |
| 10 | **Filing history stays private. Proprietorship GSTINs are masked in public.** | Carried over (§7). |
| 11 | **Fix L3. "GSTR-3B within 90 days" is wrong for most MSMEs.** | Carried over (§2.4). It is now a private evidence item, not a grade. |

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

## 1. Where trust stands (measured 2026-10-31)

### 1.1 The ladder today

| Highest rung | Firms | Share | What put them there |
|---|---|---|---|
| L0 Listed | 7,161 | 5.4% | in a source, no identifier |
| L1 Legally exists | 120,025 | 90.9% | Udyam registration for almost all of them; 1,662 have a CIN; 54 a GSTIN |
| L2 Permitted | 1 (2 hold the evidence) | 0.0% | a COS-8/9 licence. CDSCO's register holds 3 cosmetics licences against ~880 drug ones (0001) |
| L3 Operating | 0 | — | needs live GST |
| L4 Corroborated | 4,843 | 3.7% | see below |
| L5 / L6 | 0 | — | not built |

**What the 4,843 L4s rest on:**

| Basis | Firms | Holds up? |
|---|---|---|
| CHEMEXCIL membership alone | 2,018 | **No.** It is a paid export-council directory, and 0 of these firms have any identity evidence |
| CHEMEXCIL + their own website (S14 crawled the link CHEMEXCIL printed) | 1,266 | **No.** One publisher, plus the firm talking about itself |
| S17 + S17b (both Udyam) | 1,090 | **No.** One registry, counted twice |
| Everything else (MCA, exhibitors, CDSCO, NABL with another publisher) | ~470 | **Mostly yes** |

**3,408 of the 4,843 (70%) have no identity rung.** The ladder lets a firm reach L4 without
first reaching L1.

### 1.2 Defects found 2026-10-31

1. **Dead companies read as "legally exists".** S03 lists 1,609 companies. 783 are Active.
   616 are Strike Off, 44 Dissolved, 22 "Converted to LLP and dissolved", and 2 Liquidated.
   The rest are Amalgamated (91) or Converted to LLP (48), which are successions, not
   deaths. `trust.py` treats any CIN as identity, so **826 entities whose MCA status is not
   Active** carry "Legally exists — CIN …". 21 of them are at L4.
2. **Expired accreditations count.** 258 of 6,682 NABL certificates are past
   `certificate_valid_till`, and the lab still lists as accredited.
3. **Trust sorts results.** Eight templates in `pipeline/query_templates.yaml` use `ORDER BY trust
   DESC`, and two of them (lines 184 and 210) sort by distance first. That puts L4s on top
   wherever they appear.
4. **The card chip says "Entity verified"** (`serve/index.html:901`) on Udyam membership
   alone.
5. **`adverse_signal` is empty.** The schema exists and nothing writes to it.
6. **The trust figures in `catalog/snapshots/latest.json` are stale** (SOURCES_PLAN §1.2).

### 1.3 What the new grade would show today (prototype, same data)

| Grade (§6) | Firms | Share | Of today's L4s |
|---|---|---|---|
| GST verified | 0 | — | — |
| Corroborated by 3+ publishers | 6 | 0.0% | 6 |
| Corroborated by 2 publishers | 247 | 0.2% | 247 |
| Registered (one registry, one publisher) | 127,379 | 96.5% | 1,094 |
| Listed only (no registry) | 3,715 | 2.8% | 3,481 |
| Flagged: struck off or dissolved, no other live registry | 683 | 0.5% | 15 |

This is the honest picture: **131,625 of 132,030 firms (99.7%) appear in exactly one
publisher.** Our sources barely overlap, so no counting rule can tell most firms apart yet.
The grade will only differentiate once sources that overlap the Udyam population land
(§10, T3). The single biggest one is an MCA bridge: 11,526 Udyam firms have a company or
LLP name (9,579 "Private Limited", 1,488 LLP, 459 "Limited"), and each should have a CIN.

### 1.4 Pieces that already exist

| Piece | Where | State |
|---|---|---|
| Trust ladder L0–L6 with `trust_rungs`, reasons, demotion | `pipeline/trust.py`, `trust_state` | live. Replaced by §6 in T1 |
| `gst_record` (history, one row per check), `adverse_signal`, `person` | `sql/001_schema.sql` | schema only, 0 rows |
| Rung-0 GSTIN checks | `pipeline/gstin.py` → `gstin_check` | live. 54 GSTINs: 36 clean, 5 state mismatches, 13 personal PANs |
| S16 GSTIN verification | `catalog/sources/s16_gstin.yaml` | **blocked on a provider key** |
| GSTIN harvest from websites (0010) | `adapters/s14_company_sites.py` | live. All 54 GSTINs we hold came from here |
| CIN on 1,662 entities | S3 (1,609), S14 (63) | MCA resource gated on data.gov.in |
| COS-8/9 licences | S1, S2, `catalog/rti/0001` | RTI to Gujarat FDCA drafted, **not filed** |
| NABL labs | S11 | live. 6,424 certificates valid, 258 expired |
| Trade fairs | S8 | HPCI India 2026 (69), Cosmetics Ingredients Expo 2025 (139) |
| Claimed certifications and capabilities (tier 2) | S14 → `CLAIMS_*` | live, unverified by design |
| Card UI with an L-score, ladder drawer and "Entity verified" chip | `serve/index.html` | live on the demo |
| msme GST stub | `msme/gst.py` | dormant. **Do not reuse its logic**: it calls any 15-char string verified |

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

### 2.7 Is GSTIN the bottleneck right now? For discovery, no. At RFQ time, yes.
A buyer shortlisting suppliers asks four things in order: is it real, may and can it make my
thing, is it operating now, and has anyone dealt with it.
- **Real:** Udyam already answers this for 92% of firms, from a tier-1 registry. GST would
  strengthen it, but it isn't missing.
- **May and can make it:** this is where the graph is weakest. 2 firms hold licence
  evidence. GST doesn't help here at all.
- **Operating now:** GST status is the best single public signal for this. It decays, and
  auto-suspension for non-filing can happen in any month (Rule 21A).
- **Track record:** first-party only.

So GST earns the top of the grade (§6), but it is not what blocks a useful demo. What
blocks it is that the score misleads (§1.2) and that licence evidence is thin. GST becomes
**the** gate at the moment money moves: RFQ send, then contracting. That is why §4.2
blocks only there.

Even with a provider key, verification lights up 54 firms. The 119,120 Udyam firms carry
no GSTIN, no PAN and no Udyam number: the data.gov.in dataset publishes none of the three.
Their GSTINs can come only from name search (§3 rung 3), the MCA bridge (§10 T3, then CIN
→ PAN → GSTIN), association directories (AIPMA prints some), or the supplier and buyer
(§3 rungs 4–5).

### 2.8 The L0–L6 ladder is not an order
Each rung answers a different question: listed, exists, licensed for a category, operating,
corroborated, claimed, vouched. "Highest rung reached" ranks them as if each one implied the
ones below it. They don't:
- **L4 without L1:** 3,408 firms are "corroborated" but have no identity evidence.
- **L2 is per category.** A firm licensed for soaps is not licensed for hair dyes.
- **L3 is unreachable** without a provider, so the ladder jumps from L2 to L4.
- **L5 and L6 are first-party**, and they mean something else entirely.

The replacement (§6) keeps exactly one ordered thing, the evidence grade. It is **monotonic**:
each grade requires every grade below it. Everything that isn't ordered becomes a facet.

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

## 6. The evidence model: one grade, three facets

Replaces the 0–10 score of the PRD wireframe and the L0–L6 number on the card. The PRD's
rule still governs: "unknown evidence remains unknown" (§6.4). Every label describes **our
evidence, never the supplier**.

### 6.1 The grade: how sure are we that this firm is real, and is this firm

Ordered, monotonic, and one per firm. Each grade requires every grade below it.

| Grade | Requires | Card wording | Today |
|---|---|---|---|
| **G4 GST verified** | G1, plus a **live** GSTN check (§2.5) within 30 days: status Active, legal or trade name matches our record, and the principal-place state matches | "GST active · checked 2 d ago" | 0 |
| **G3 Corroborated (3+)** | G1, plus ≥ 3 independent publishers agree in all (§6.2) | "Found in 3 independent sources" | 6 |
| **G2 Corroborated (2)** | G1, plus 2 independent publishers agree in all (the registry counts as one) | "Found in 2 independent sources" | 247 |
| **G1 Registered** | In at least one tier-1 registry (§6.2), with no tier-1 adverse fact on it | "Registered (Udyam)" / "Registered (MCA)" | 127,379 |
| **G0 Listed** | Appears in a source | "Listed in <source>" | 3,715 |
| **Flagged** (override) | A tier-1 adverse fact (struck off, dissolved, GST cancelled, licence cancelled), and no other live registry entry | "MCA status: Struck off (as of <date>)" plus the source and the supplier's response, if any | 683 |

**Rules:**
- **G4 decays.** After 30 days the chip reads "GST active · checked <date>", and the firm
  ranks at the grade it would have without G4. The dated check stays in the drawer.
- **A stub or vendor-cache record never counts toward G4.** Only `provider_class =
  live_gstn` does (§4.1).
- **Flagged needs care.** If the dead CIN belongs to an entity that also has a live Udyam
  entry, it may be a wrong merge. That case goes to `review_queue` as
  `adverse_vs_live_registry`, not to the public card.
- **Successions are not adverse.** Amalgamated and Converted to LLP add no identity credit
  from the old CIN. The drawer notes the succession.
- **Proprietorships** reach G4 the same way. The GSTIN and legal name stay masked (§7.1).

### 6.2 Independent publishers

Corroboration counts **publisher families**, not source ids. Each source card gains a
`publisher_family` and a `kind`, and `make catalog-check` enforces both.

| Family | Sources | Kind | Counts as a registry (G1)? | Corroborates? |
|---|---|---|---|---|
| udyam | s17, s17b | tier-1 registry (Ministry of MSME) | yes | yes, once |
| mca | s03 | tier-1 registry (MCA) | yes, **only if status is Active** | yes |
| gstn | s16 | tier-1 registry | yes, via G4 | yes |
| cdsco | s01 | tier-1 licensing register | yes | yes |
| state_fda | s02 (and RTI replies) | tier-1 licensing register | yes | yes |
| nabl | s11 | tier-1 accreditation register | yes, **only if the certificate is valid** | yes |
| zed, pcb, ec, cosmos | s23, s22, s21, s20 (SOURCES_PLAN Phase 2) | tier-1 issuer registers | yes | yes |
| council | s09 CHEMEXCIL | third-party directory | no | yes |
| fair:<organiser> | s08, one family per organiser | third-party directory | no | yes |
| association:<name> | s26 SEA / AIPMA / AIGMF | third-party directory | no | yes |
| self | s14, s14b, supplier claim | the firm about itself | no | **never** |

- **Third-party directories corroborate but never establish identity.** Two directories that
  agree on a firm with no registry entry leave it at G0, with a drawer line saying so.
- **Agreement means the resolver merged the records** under 0004–0008: name plus pincode,
  district or identifier. The drawer lists which publishers agree, and on what.
- **A publisher that republishes another publisher is one family.** An exhibitor list copied
  from a council directory counts once. The card records `derived_from` where we know it.

### 6.3 The facets: what the grade doesn't answer

Each facet has a state of `verified · claimed · unknown · adverse`, and each state carries
its evidence, tier, source and date.

| Facet | Question | Evidence (tier) | Today |
|---|---|---|---|
| **Licence** | May it make *this* category? Always per category | COS-8/9 with a category endorsement (1, S1/S2/RTI); NABL scope for labs (1); `PERMITTED_TO_MAKE` from consent or EC is a **ceiling**, shown as "Pollution consent covers …", never as a licence (0020); claimed licence (2) | 2 licensed firms; 201 labs with cosmetics scope |
| **Activity** | Is it operating now? Expires 18 months after `observed_at` | GST Active (1, from G4); valid pollution consent to operate (1, s22); ZED certificate date (1); MCA status Active (1); exhibited within 12 months (2); e-invoicing enabled (1, public firms only); website live, checked <date> (3, self); *filing on schedule: private only* (§2.4) | HPCI 2026: 69; MCA Active: 783; NABL valid: 6,424; S14 sites crawled 2026-09: 1,499 |
| **Track record** | Have buyers dealt with it, and how did that go? | e-invoice QR-verified transactions (1, §8.1); RFQ response time measured by us (§8.7); buyer-confirmed outcome (2); consented GSTR-2B (1, §8.4) | none |

**Tenure is not activity.** "Udyam-registered since 2021" is shown under the grade, never as
Activity. The dataset's registration dates run 2020–2024, which says nothing about 2026.

**Supplier claim (old L5)** isn't a facet. A claim that passes §8.6 adds a "Profile claimed by
the company" line, and its GSTIN goes through G4 like any other.

### 6.4 The card

```
Registered (Udyam) · Found in 2 independent sources
Licence ? for skin creams · Active: exhibited HPCI 2026 · Track record —
```

There's no number. If a single count is ever wanted, use "2 of 3 facets evidenced", a count
that makes no claim about quality.

### 6.5 Ranking

1. **Fit:** match rank, evidence tier of the match, then distance for "near" questions (0028).
2. **Then the facet that matters for the question:** Licence for "who can make", Activity
   for suppliers, the grade for everything else.
3. **Then name.**

Trust never sorts first. A lint golden query (`trust_never_orders_results`) fails any
template whose `ORDER BY` puts a trust field ahead of fit.

### 6.6 Storage and migration

- `trust_evidence` (§9) holds one row per fact. Grade and facets are computed from it nightly,
  replacing what `trust.py` does today.
- Neo4j `Company` gets `trust_grade`, `trust_facets` (public states only) and
  `trust_reasons`. `trust_level` and `trust_rungs` stay for one release so nothing breaks,
  then go. `CLAUDE.md`, `glossary.yaml`, the golden queries and the agent skill move to
  `trust_grade` in the same change.
- Public demotion requires a **tier-1** adverse fact. Lower-tier adverse signals are private
  warnings at most.

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
- **`trust_evidence`** (T1, the PRD §9.1 entity): `entity_id, facet (grade|licence|activity|track),
  signal_type, value jsonb, source_id, publisher_family, tier, provenance_class,
  visibility (public|private|consented|supplier_only), observed_at, expiry_at, confidence,
  superseded_by`. Append-only, like `crosswalk`.
- **`adverse_signal`** (exists, 0 rows) gets its first writers in T0: `mca_struck_off`,
  `mca_dissolved`, `mca_liquidated`, `nabl_expired`. Later: `gst_cancelled`, `licence_cancelled`.
- **`gst_record`** (exists, append-only) adds `provider_class` (`live_gstn` | `vendor_cache` |
  `stub`), `raw_sha256`, `einvoice_enabled`, `filing_frequency`, `trigger`
  (render/detail/shortlist/rfq/claim/free_check), `cost_inr` and `latency_ms`. It already has
  `taxpayer_type`.
- **`gstin_candidate`** (T2): name-search results waiting on the link rule (§3 rung 3). They
  link only through `crosswalk` or `review_queue`.
- **`verification_spend`** (T2): the ledger behind the budget guard, reconciled monthly against
  vendor invoices.
- **`trust_feedback`**, **`supplier_response`** and **`consent_record`** (T7).
- Proprietorship GSTINs get `person`-grade handling: erasable, with the consent basis recorded.

**Catalog:** every source card gains `publisher_family` and `kind` (§6.2). `make
catalog-check` fails a card without them. S16 moves from `blocked` to `stub` in T2, and to
`live` with vendor cards in T4.

**Code** (`cosmetics/trust/`, with no cosmetics-specific imports, so `msme/gst.py` can be
replaced by it):
- `evidence.py` (T1): turns sources into `trust_evidence` rows. `grade.py` computes §6.1
  and `facets.py` computes §6.3. `pipeline/trust.py` becomes a thin caller.
- `gst/adapter.py` (T2): the interface, normalised to GSTN's `searchTaxpayer` field names,
  with two methods, `verify(gstin)` and `search_by_name(name, state)`.
  - `gst/providers/stub.py` returns deterministic fake records marked `stub`.
  - `gst/router.py`: breaker, hedging, shadow sampling (§4.4).
  - `gst/cache.py`: per-field TTLs (§4.3), single-flight.
  - `gst/link.py`: the rung-3 auto-link rule.
- `budget.py` (T2): caps and the ledger.
- `discovery.py` (T4): the §3 rungs.
- `qr.py` (T7): e-invoice JWS verification against IRP public keys, fetched from the official
  portal and pinned.
- **API** (PRD §9.2): `GET /vendors/{id}/trust` (public fields only), `POST
  /vendors/{id}/verify` (budgeted, rate-limited), `POST /vendors/{id}/report`, `POST
  /trust/check-gstin` (match without disclosure).
- **Agent:** `skills/jarvis-cosmetics-search` reads `/trust` and quotes its states. It gets no
  paid-call tool.

**Golden queries:**

| Phase | Query |
|---|---|
| T0 | `no_inactive_cin_as_identity` |
| T0 | `corroboration_requires_identity` |
| T0 | `own_site_never_corroborates` |
| T0 | `trust_never_orders_results` |
| T1 | `no_private_trust_fields_in_neo4j` |
| T1 | `every_chip_has_evidence` |
| T2 | `stub_never_verifies` |
| T2 | `no_unmasked_proprietorship_gstin_public` |
| T4 | `every_gst_badge_has_a_live_record` (PRD §11.2) |

---

## 10. Delivery

Phases are named T0–T7 so they don't collide with SOURCES_PLAN's numbered phases. The old
2.1–2.4 map onto them: 2.1 → T2 + T4, 2.2 → T4, 2.3 → T0 + T1, 2.4 → T6 + T7.

| Phase | Scope | Cost | Blocked on | Exit criteria |
|---|---|---|---|---|
| **T0. Stop the number misleading** | §10.1 | ₹0, 2–3 days | nothing | 0 inactive CINs counted as identity; 0 firms corroborated without identity; L4 count under the new rule ≤ ~470; no template sorts on trust |
| **T1. Evidence profile** | §10.2 | ₹0, ~1 week | nothing | every chip on the card traces to a `trust_evidence` row; grade and facet distribution in the catalog snapshot |
| **T2. GST behind a stub** | §10.3 | ₹0, ~1 week | nothing | stub verify and name search run end to end on the 36 clean GSTINs; `stub_never_verifies` passes; swapping in a provider is one file plus a card |
| **T3. Free sources that corroborate** | §10.4 | ₹0 | data.gov.in MCA access for the bridge; the rest rides SOURCES_PLAN | share of firms with ≥ 2 publishers measured after each source lands |
| **T4. Live GST** | §10.5 | ≈ ₹10k pack + ₹5k/month cap | Lattix's legal entity and GSTIN; budget sign-off | every GST chip traces to a `live_gstn` record; GST coverage of *surfaced* vendors ≥ 30%; name-search false links < 2% on a 100-row audit |
| **T5. Licences by RTI** | §10.6 | ₹10 per request | Sarvesh files them | Licence facet verified for > 2 firms; tier-1 `PERMITS` edges exist (0012) |
| **T6. Certificates at the issuer** | §10.7 | IAF "Basic 499" (150 credits a year) | budget sign-off | every verified certification chip has an issuer lookup behind it |
| **T7. First-party evidence** | §10.8 | per use, plus a lawyer | real users; lawyer review | ≥ 1 claimed profile; ≥ 1 QR-verified transaction; correction loop live |

**Order:** T0 → T1 → T2 now. All three cost ₹0, depend on nothing outside the repo, and take
about 2.5 weeks together. **T0 must land before SOURCES_PLAN Phase 3**, which adds Cosmoprof
and three association directories: under today's rule each of those would mint more
council-only L4s. T3 rides along with SOURCES_PLAN. T4–T7 start when their blockers clear.

### 10.1 T0 — stop the number misleading

1. **Ranking.** Take `trust DESC` out of the eight templates and rank per §6.5. Add the
   `trust_never_orders_results` lint.
2. **Independence.** Add `publisher_family` to every source card. `trust.py` counts families, never
   counts `self`, and gives corroboration credit only to firms with identity.
3. **MCA status.** Strike Off, Dissolved, Liquidated and "Converted to LLP and dissolved"
   write a tier-1 `adverse_signal`, and the CIN stops counting as identity. Where the same
   entity also has a live registry entry, write a `review_queue` row instead
   (`adverse_vs_live_registry`). The source card states the dataset's as-of date, since a
   data.gov.in snapshot lags MCA.
4. **NABL validity.** A certificate past `certificate_valid_till` doesn't count as a registry
   entry or as a Licence fact for labs. The drawer shows "Certificate expired <date>".
5. **Card wording.** "Entity verified" becomes "Registered (Udyam)" or "Registered (MCA)".
6. **Measure.** Run `make catalog` so `latest.json` carries the new distribution, and update
   the s16 and glossary entries.

### 10.2 T1 — the evidence profile

- `trust_evidence` plus `evidence.py`, `grade.py` and `facets.py` (§9). `trust.py` keeps its CLI.
- **Activity, from data already held:** HPCI India 2026 exhibitors (69); MCA status Active
  (783); NABL certificates in date (6,424); S14 site crawled 2026-09 (1,499, tier 3, self).
  Each item expires 18 months after `observed_at`.
- **Neo4j:** `trust_grade`, `trust_facets` and `trust_reasons` on `Company`. Templates return them.
- **UI:** the card shows the grade line and the facet line (§6.4). The ladder drawer becomes an
  evidence drawer: each facet with its rows, sources and dates.
- **Agent:** the skill and the schema card quote grade and facets, never a number.
- **Migration:** `trust_level` and `trust_rungs` are kept for one release, then removed together
  with `CLAUDE.md`'s "filter with `2 IN trust_rungs`" rule.

### 10.3 T2 — GST behind a stub

- `sql/003_trust.sql`; the `cosmetics/trust/gst/` package (§9).
- **The stub provider** returns deterministic records keyed by GSTIN hash: a realistic status
  mix, plus name and `pradr` drawn from our own record. That lets the link rule be tested
  for matches and for mismatches. It writes `gst_record` with `provider_class = 'stub'`.
- **Stub name search** returns 0–3 candidates, including a deliberate near-miss name, so that
  `review_queue` sees real work.
- **The rung-0 gate runs before every call.** The 5 GSTINs with a state mismatch never reach a
  provider. The 13 personal-PAN GSTINs are checked only on a buyer action (§4.2).
- **Cache, single-flight, budget ledger and triggers** are built as §4.2–§4.4 describe. The
  stub costs ₹0 and still writes `verification_spend` rows at ₹0, so the ledger is tested.
- **Drawer:** "GST evidence unavailable — verification is not connected yet" until T4.
  Stub results are visible only in dev mode, labelled "STUB". The public demo never shows them.

### 10.4 T3 — free sources that corroborate

Chosen for overlap with the Udyam population, since 99.7% of firms sit in one publisher today.

| Source | Where it's planned | Grade | Facet | Expected reach |
|---|---|---|---|---|
| **MCA bridge**: CIN and status for Udyam firms with a company or LLP name | **new**; same data.gov.in resource as s03 | 2nd registry → G2; adverse → Flagged | Activity (status Active) | up to 11,526 Udyam firms. Feeds T4's CIN → PAN → GSTIN |
| ZED certified MSMEs (s23) | SOURCES_PLAN Phase 2 | 2nd registry (it requires Udyam, joins on name + pincode) | Activity (certificate date) | unknown until fetched; Bronze is weak |
| GPCB / MPCB consents (s22) | SOURCES_PLAN Phase 2 | 2nd registry | Activity (valid consent to operate); Licence ceiling (0020) | Gujarat and Maharashtra plants |
| Environmental clearances (s21) | SOURCES_PLAN Phase 2 | registry | Licence ceiling only | large plants |
| COSMOS (s20) | SOURCES_PLAN Phase 2 | issuer register | certification verified | small |
| Goa loan and own-manufacturer lists (s02) | SOURCES_PLAN Phase 3 | registry | Licence verified | 9 firms |
| Cosmoprof 2026 (s08), SEA / AIPMA / AIGMF (s26) | SOURCES_PLAN Phase 3 | third-party corroboration | Activity (exhibited) | hundreds; AIPMA prints some GSTINs (§3 rung 1) |
| Udyam re-sweep | SOURCES_PLAN Phase 1 | no new family | — | landed pages allow re-checks later |

### 10.5 T4 — live GST

These are the old 2.1/2.2 items, unchanged in substance:
- **Vendors:** due diligence per §4.1, trials with two providers, then live `verify` on the
  §4.2 triggers.
- **Rung 2:** CIN → PAN → GSTIN for every Active CIN: the 783 held today, plus T3's bridge.
- **Rung 3:** name search on detail-open, capped at 300 a month until the false-link audit
  passes.
- **Private signals:** "filing on schedule" computed against the taxpayer's own frequency
  (§2.4), and "e-invoicing enabled".
- **Match without disclosure:** the free check (§8.10).

### 10.6 T5 — licences by RTI

- File `catalog/rti/0001` (Gujarat). Then the per-state template to Maharashtra, Tamil Nadu,
  Himachal, Uttarakhand and Delhi.
- File a second request per state for **cancelled or suspended** licences: tier-1 adverse
  evidence (§8.5).
- Replies land as s02 rows: Licence facet verified, and `PERMITS` edges at tier 1.

### 10.7 T6 — certificates at the issuer

- IAF CertSearch, run lazily on shortlisted suppliers that claim ISO 9001, 22716 or 15378.
- COSMOS, Leaping Bunny and PETA lists in bulk where they publish one.
- A claim that fails at the issuer stays private and goes to review. It is never shown as fake.

### 10.8 T7 — first-party evidence (with real users)

- **Supplier claim** (§8.6): GSTIN plus a domain email or DNS TXT record, and optionally a
  penny-drop.
- **Track record:** RFQ response time measured on our rail (§8.7); e-invoice QR vouching (§8.1);
  consented compliance badge (§8.2); buyer-consented GSTR-2B (§8.4).
- **Alerts and corrections:** status-change alerts to shortlisters (§8.9); the correction loop and
  right of reply (§7.4).
- **Legal:** privacy notice and terms (§7.7), with a lawyer.

---

## 11. Decisions only Sarvesh can make

1. **Approve the model** (§6): one grade (GST verified, then 3+, 2, registered, listed, with
   Flagged as an override) plus three facets. The L-number leaves the card.
2. **Public wording for a dead company:** "MCA status: Struck off (as of <date>)" (proposed),
   or a softer "Not active on the MCA register"?
3. **data.gov.in MCA access**, for the T3 bridge and for s03. It is requested from the same
   account as the Udyam key.
4. **Vendor accounts** (T4): trials with gstinapi.com, Cashfree and Infyner, and quotes from
   two GSPs (Quicko/Sandbox, Masters India) using the §4.1 questionnaire. These need Lattix's
   entity and GSTIN.
5. **Budget:** ₹10k one-off plus a ₹5k/month cap to start (T4), and IAF Basic 499 (T6)?
6. **Drop the 0–10 score** on the landing page too (§7.6)?
7. **Public wording for non-active GST:** "Could not be confirmed active, checked <date>"
   (proposed), or the exact status?
8. **Lawyer review:** DPDP basis for processing proprietorships; the display matrix; the CICRA
   boundary; buyer and supplier terms; the Samadhaan question.
9. **File RTI 0001** (Gujarat), as only an Indian citizen can, and approve the per-state template.

## 12. Risks

| Risk | Mitigation |
|---|---|
| A wrong merge pins a struck-off CIN on a live firm | Flagged only when no live registry entry exists on the same entity; otherwise `review_queue` (§6.1). The dataset's as-of date is shown |
| Directories copying each other inflate corroboration | Counted by publisher family; `derived_from` on cards; own site never counts (§6.2) |
| A stub result reaches the public demo | `provider_class = stub` never counts toward G4; dev-mode only; `stub_never_verifies` golden query (§10.3) |
| The grade stays flat (96.5% at G1) and looks useless | Say so on the card honestly; T3 sources are picked for overlap with Udyam; measure the share at G2+ after each lands |
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
