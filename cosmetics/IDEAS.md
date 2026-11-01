# Lattix — ideas from the sidelines

Noted 2026-10-31 while testing the demo end to end and writing the investor script
(DEMO_SCRIPT.md). These are **ideas, not decisions**. Each says why it might matter, the
evidence behind it, what it depends on, and a rough size (S: days, M: weeks, L: a
quarter). When one is approved, it moves into SOURCES_PLAN.md or TRUST_PLAN.md (or a new
plan) and gets a decision record. Facts marked *verify* came from memory or press, not a
primary source.

---

## The five I'd do next

1. **Turn the compare tray into "Request quotes from these 3"** (§1.1). It's the
   smallest step from search to a procurement event. It starts the first-party data
   (price, MOQ, lead time, response time) that no directory has and that makes the moat.
2. **Vendor-master health check** (§3.1). A buyer uploads their supplier list and gets
   each vendor graded, with struck-off and GST-cancelled alerts. It sells to procurement
   teams with almost no new code: the 683 struck-off flags already exist.
3. **WhatsApp, with Hinglish voice notes** (§1.4). The demo already answers Hinglish in
   Hinglish, and WhatsApp is where Indian MSMEs actually trade.
4. **"Claim your profile", plus a demand radar for suppliers** (§2.1, §2.2). This makes
   the product two-sided and gives suppliers a reason to pay.
5. **The data upgrades that move "Listed" firms** (§4). Run the MCA bridge (built), verify
   CINs and GSTINs taken from firms' own sites, re-sweep Udyam with chemical codes, and
   fix pincode coordinates. Today 51 of the 55 glycerin suppliers read "Listed".

---

## 1. Demand side — buyers (brands, contract manufacturers, exporters)

### 1.1 RFQ from the compare tray (M)
"Request quotes from these 3" sends one structured request (product, spec, quantity,
target date) to the shortlisted suppliers by email or WhatsApp, and collects the replies
into a quote table.
- **Why:** the demo ends at "contact them for a quote", and every answer says price, MOQ
  and lead time aren't in the data. The RFQ rail (SOURCES_PLAN s31, decision 0025) is the
  only source of those.
- **Depends on:** business contacts, which only 810 firms have, so outreach needs a
  fallback channel; consent and terms (TRUST_PLAN §7.7).
- **Grows:** the response-time signal (TRUST_PLAN §8.7) and outcome data.

### 1.2 Bill-of-materials builder (M)
The demo's first answer is already a plan: standards, ingredients, makers, labs. Make it
an object. "Launching a niacinamide serum" becomes a checklist of INCI ingredients, the
packaging spec, the contract manufacturer, the lab and the BIS standards, each line with
its shortlist. Share it, export it as a PDF, and send each line as an RFQ.
- **Why:** this is the procurement loop as the PRD frames it, and the BOM templates are
  already decision 0026.

### 1.3 "Make me something like this": product label to sourcing plan (M)
Photograph a competitor's label. It reads the INCI list (the 35,864-ingredient vocabulary
already resolves names and synonyms), checks each ingredient against the India and EU
rules, and finds suppliers for each.
- **Why:** a "wow" for D2C founders, and it reuses the ingredient resolver, the
  restriction templates and the supplier search unchanged.

### 1.4 WhatsApp and voice, in the languages clusters speak (M)
Ask on WhatsApp, including voice notes, in Hinglish, Gujarati, Hindi or Marathi, and get
card summaries back with a link to the full evidence.
- **Why:** Ahmedabad–Vapi and Palghar–Thane run on Gujarati and Marathi, and owners send
  voice notes. The demo already understands "mujhe Ahmedabad ke paas glycerin supplier
  chahiye".
- **Depends on:** the WhatsApp Business API; the iMessage bridge in the Jarvis platform is
  a pattern to copy.

### 1.5 Export-readiness report (M)
Give it a formula, or an INCI list. It returns every ingredient restricted, capped or
banned in the EU (with the annex entry), and later the GCC and ASEAN rules, plus what
India requires.
- **Why:** step 7 of the demo (salicylic acid, 0.5%) is this in miniature. Exporters pay
  consultants for exactly this.
- **Careful:** it's regulatory information, not certification. Say so on every page.

### 1.6 Testing marketplace (M)
Book a test through a lab's scope: stability, challenge, heavy metals. Compare price and
turnaround once labs quote through the rail.
- **Why:** the demo shows 191 in-date cosmetics labs, but NABL publishes only person
  contacts, which we keep private. So a buyer can't reach a lab from the card today.
- **Fix in passing:** crawl lab websites for business contact lines (S14 already does
  this for companies).

---

## 2. Supply side — MSMEs who sell

### 2.1 Claim your profile (M)
Firms are already in the graph (1.3 lakh), so invite them by Udyam number or GSTIN.
- **The supplier adds:** products, MOQ, capacity, photos and certificates.
- **We verify:** GSTIN, plus a domain or bank proof (TRUST_PLAN §8.6, L5).
- **They get:** a "profile claimed" line on their card, and a lead inbox.
- **Why:** it's the only way to fix "Listed": a firm's own claim, verified, beats any
  directory. It is also a free acquisition channel for the supply side.

### 2.2 Demand radar (S once there's traffic)
Show a supplier anonymised demand near them, for example "43 buyers looked for airless
pumps within 50 km of Vapi this month".
- **Why:** it's the reason a supplier pays. It comes from search logs we already have
  (only aggregates, never who asked).

### 2.3 Win-rate coaching (L)
Once RFQs flow, tell a supplier why they lose: response time, missing licence, price band.
- **Careful:** this is private feedback only. Never publish a supplier's performance
  without consent (TRUST_PLAN §7.4).

---

## 3. Trust and verification as products

### 3.1 Vendor-master health check (S–M)
A buyer uploads a supplier list as CSV (name, GSTIN, CIN or address). We resolve each row
against the graph and return its evidence grade. We flag:
- struck-off or dissolved companies (683 today);
- lapsed NABL certificates;
- GST status, once T4 is live.
A watchlist then alerts on any change (TRUST_PLAN §8.9).
- **Why:** it's useful the day it ships, it uses what's built, and a procurement or
  compliance team will pay for it. The entity resolver is already the hard part.

### 3.2 MSME-supplier classification for the 45-day rule (M, *verify*)
Section 43B(h) of the Income-tax Act (from FY 2023-24) lets a buyer deduct a payment to a
micro or small enterprise only if it's paid within the MSMED Act's time limit, at most 45
days. Companies also report MSME dues on MCA's MSME-1 form.
- **What it means:** a buyer needs to know which vendors are registered micro or small
  enterprises. The graph knows Udyam registration for 1.2 lakh firms.
- **Gap:** the Udyam category (micro, small, medium) isn't in the data.gov.in rows we
  hold. Check whether the dataset or the portal's public verification exposes it.
- **Why:** a compliance reason to upload a vendor list, which leads straight into §3.1.

### 3.3 Supplier-verification API (M)
Given a name plus a GSTIN or CIN, return the evidence grade and its facts: a KYB call for
brands onboarding vendors, and for platforms.
- **Careful:** it must never become a credit score. CICRA (TRUST_PLAN §7.5) keeps out
  loan, repayment and bureau data.

### 3.4 Trade-fair mode (S)
At Cosmoprof India or HPCI, scan a visiting card or a booth QR and get the firm's evidence
card on the spot.
- **Why:** fairs are where the industry discovers suppliers today (S8 already lands
  exhibitor lists). It's also a natural partnership with organisers.

---

## 4. Data — moving firms out of "Listed"

| Idea | Size | Evidence |
|---|---|---|
| **Run the MCA bridge** (`make mca-bridge`, built) | S once data.gov.in answers | 13,926 company names: 11,627 Udyam firms gain a second registry and status; 2,319 directory and website firms gain a first. 711 of them have a place, so they can be matched on name plus place |
| **Verify identifiers firms publish themselves** | S | Molkem's drawer lists its own GSTIN and CIN, taken from its website, yet the firm reads "Listed". A CIN or GSTIN is an exact key, so an MCA or GST lookup by it needs no name matching. That's 54 GSTINs and 63 CINs today, and more as S14 crawls |
| **Re-sweep Udyam with chemical codes, landing every page** (SOURCES_PLAN Phase 1) | M, ~7 h of fetching | 51 of 55 glycerin suppliers are "Listed", because the first sweep threw away chemical NIC codes. It also adds Kerala, West Bengal and Assam |
| **Fix pincode coordinates** | S | 4,742 of 19,238 pincodes (25%) share placeholder points in GeoNames. All of Palghar sits about 65 km off, so "near Palghar" ranks wrongly. India Post's pincode directory on data.gov.in carries a latitude and longitude per post office |
| **Rank noisy registry names last** | S | "Cosmetics manufacturers in Baddi" opens with "ANIL KUMAR" and "APEX ELECTRONICS": self-declared Udyam codes, sorted alphabetically. Rank by evidence of cosmetics activity (core NIC, a website, a directory listing) and flag name and activity mismatches |
| **Ingredient functions for common preservatives** | S | "Alternatives to parabens" returns nothing, because CosIng carries no function tag for methylparaben in our copy. Fill functions from CosIng's own function field, or curate the top 50 |

---

## 5. Intelligence products (for investors, strategics, government)

- **Cluster reports (M).** Step 9 of the demo, a firm count by cluster, is the seed of a
  paid report: capabilities, packaging depth, labs and who-makes-for-whom per cluster.
  The audience is PE funds, large FMCG doing supplier diversification, and state
  industry departments.
- **Who-makes-for-whom (M).** Loan-licence links (CDSCO), product labels (SOURCES_PLAN
  s24) and Goa's lists (s02) build the relationship map that contract manufacturing
  keeps private.
- **Regulatory gap watch (S).** Track "EU-restricted ingredients on Indian shelves"
  (demo step 12) over time, and alert brands when an ingredient they use moves in the
  EU annexes.

---

## 6. Go-to-market and partnerships

- **Contract manufacturers as a channel.** A contract manufacturer buys ingredients and
  packaging for 50 brands. Give them Lattix free for sourcing, and their brands follow.
- **Cluster associations and state MSME departments.** Baddi, Vapi, Haridwar and Palghar
  have industry associations. "Your cluster's searchable supplier map" is a partnership
  pitch, and a free source of claimed profiles.
- **ZED and TReDS partners.** Firms with a ZED certificate are a ready "verified" segment
  (SOURCES_PLAN s23). TReDS platforms could finance suppliers who win RFQs. This is
  embedded finance, with CICRA care.
- **Content as acquisition.** "The Cosmetics Rules 2020 in plain English", "What a COS-8
  licence takes", "Every NABL cosmetics lab, by state": the answers already exist in the
  graph.
- **Expansion verticals.** The engine isn't cosmetics-specific:
  - food processing has a public licence register (FSSAI), which would fill exactly the
    licence gap cosmetics has;
  - pharma has CDSCO, already partly loaded;
  - textiles already runs as the msme project on the same Udyam pipeline.

---

## 7. Platform and reliability — found while testing

### Fixed on 2026-10-31

| Problem | Effect in the demo | Fix |
|---|---|---|
| The skill's template table never listed the distance templates | Every "near" question returned an unranked list. One reply called a Mumbai firm "the closest fit" to Ahmedabad | Added both, plus a rule for "near", "nearest" and "ke paas" (SKILL.md) |
| "Baddi" also matched "Baddi Jhallian", a Punjab village 39 km away | "Near Baddi" ranked Rupnagar firms first | An exact place name now beats a prefix match (`router._place`) |
| Keyword fallback sent "glycerin near Ahmedabad" to the packaging template | A wrong answer if the model gateway failed | A deterministic distance rule ahead of the fuzzy match, and Hindi "X ke paas" phrasing |
| `.cards{display:grid}` overrode the `hidden` attribute | "Table" showed the table **under** the cards, and "show all" cards leaked | A global `[hidden]{display:none !important}` |
| Hero figures were typed into the page | "201 accredited labs", after 10 had lapsed | `/api/stats` counts them live (191) |
| No starter questions | An investor opening the link alone faced an empty box | Six starter chips from the tested question list |
| "No licence on file" on labs, packaging and traders | Read as a red flag for a licence they never need | Shown only for manufacturers |
| A follow-up ("only the ones near Baddi") on labs | The reply leaked "the `near` parameter" and asked the user for a pincode | The skill says only ingredients and packaging rank by distance; follow-ups re-run the template themselves |
| Stage labels were function names | "Running ingredient restriction india" | Plain labels per query, e.g. "Checking the Cosmetics Rules 2020", with a test that every template has one |
| Distance not shown; the table showed evidence text as columns | km invisible; a wall of text in table view | "· 22 km" on cards and in compare; internal columns hidden in the table |
| Price questions pointed buyers to IndiaMART | Sent the buyer to a competitor | Quote-first wording in the skill |
| The demo checks tested the router, not the agent the demo uses | The distance bug stayed invisible to every test | An agent-path test per demo question, plus `make demo-rehearse` against the live demo |

### Still open

- **No model-free fallback in the demo agent.** If the LiteLLM gateway fails, the page
  errors. The router already has keyword routing (tested), so the agent could fall back
  to it and show rows without prose. (S)
- **Per-question cost isn't measured.** The gateway's spend log holds only 3 untagged
  entries. Tag requests with metadata (template, demo or console) and report cost per
  answer: an investor will ask. (S)
- **Agent model.** The demo answers on `openrouter/anthropic/claude-sonnet-4.6`. Before
  switching to a newer model, run `make demo-check` and `make demo-rehearse` on it and
  compare latency, cost and answers. (S)
- **Generate the skill's template table from `query_templates.yaml`** so the two can't
  drift again. The agent-path test catches drift; generation prevents it. (S)
- **The agent sometimes uses jargon** ("solid G3 confidence grade", "the tool") despite
  the demo instructions. Add a check to the rehearsal for the words "G0"–"G4", "tool"
  and "template" in replies. (S)
- **The opener takes 22–28 seconds.** It chains five or six queries. Run independent
  queries in parallel, or stream the first finished block while the rest run. (M)
- **Thin spots to avoid live:** third-party manufacturers in Baddi (no Baddi website
  claims it, so the answer falls back to registry firms); niacinamide "near" any cluster
  (the suppliers sit in Mumbai and Thane); "alternatives to …".
