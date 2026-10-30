# Adjacent-industry sources — plan for closing the procurement loop

Plan, not as-built. It covers what the graph needs beyond finished-goods cosmetics: the
ingredients, chemicals, packaging and contract-manufacturing data that sit on a cosmetics
bill of materials, plus the commercial and outcome data the PRD's procurement loop needs.
Written 2026-10-26. Store numbers were measured on 2026-10-25. Every source verdict comes
from a live probe made 2026-10-25/26 (read-only GETs: no forms submitted, no captchas,
no logins) unless the entry says otherwise.

**Status 2026-10-29: approved.** The decisions are recorded as `catalog/decisions/0017`–`0028`,
with amendments to `0006`, `0007` and `0011`. Three corrections came from evidence found
while recording them, and they are applied below:
- NIC 23109 is not packaging either;
- 20233 is not a curated supplier code;
- the generic-token list is not widened.

Read `CLAUDE.md`, `catalog/glossary.yaml` and `TRUST_PLAN.md` first. This plan keeps every
hard rule in them and relaxes none. No adapter is built until this document is agreed.
The decisions in §5 then become decision records `0017+`, and every new source gets its
card with its `do_not:` block written **before** its adapter.

---

## 0. Decisions in one screen

| # | Decision | Why |
|---|---|---|
| 1 | **Widen the scope to the cosmetics bill of materials, not to "chemicals".** A chemical or packaging firm enters the answerable graph only when evidence links it to a material that goes into a cosmetic. Everything else is staged in Postgres. | The 2026-10-08 glycerin session asked for exactly this: record the scope change as a decision before widening any source. A general chemicals graph would be 10× the rows with a near-zero hit rate on cosmetics questions. 0001 already does the same for pharma. |
| 2 | **The material vocabulary is the join key.** Keep INCI for ingredients. Add trade names, synonyms and CAS numbers so "glycerine", "glycerol" and "56-81-5" all resolve. Give packaging a controlled vocabulary (`bottle.pet`, `pump.airless`, `tube.lami` …). Tie both to HS and NIC codes. | Every miss in the glycerin session was a vocabulary miss. Sources speak trade names, INCI, HS codes and NIC codes, and today nothing translates between them. |
| 3 | **Re-sweep Udyam, and land the raw pages this time.** Widen the NIC set and fix the packaging codes. | The cheapest large win: tier 1, ₹0, about 6–7 hours. The first sweep threw non-matching rows away, so any widening costs a full re-sweep. Landing the raw pages makes this the last one. |
| 4 | **Tier-1 product evidence comes from environmental and pollution regulators.** Environmental-clearance minutes, Maharashtra pollution-board committee minutes, the Gujarat pollution-board consent register and ZED certificates. | Regulators don't say *what* a chemical firm sells anywhere else. These documents list products with capacities. |
| 5 | **Who makes for whom comes from labels and state licence lists, not filings.** | Decision 0015 found that brands keep manufacturer names out of their SEBI filings. Product labels are a different matter: they must name the manufacturer, and brand websites republish them. Goa's drug regulator publishes loan-licence lists that give the direction 0002 could not find. |
| 6 | **No marketplace scraping. IndiaMART stays `not_built`, now for legal reasons.** | Its terms ban automated retrieval and its robots.txt blocks our crawler. TradeIndia, Amazon and Nykaa are the same. The legal route to the same data is supplier-claimed profiles and RFQ responses. |
| 7 | **No importer-named Indian customs data.** Use aggregate import statistics. | Customs Act s.135AA makes publishing importer-identifying customs data an offence. tradestat's HS-8 aggregates are lawful and free. |
| 8 | **Price benchmarks stay out of the graph and are never shown as a quote.** | Same rule as msme/marketdata: numbers stay out of the graph. An import unit value is a ceiling and a trend, not a price anyone will sell at. |
| 9 | **MOQ, lead time, real price and outcomes are first-party data: the RFQ rail is a source with its own card.** | No public source carries them. Probes of all 30+ candidates confirmed it. The loop fills these fields for exactly the suppliers it touches, which are the ones that matter. |
| 10 | **Cheap fixes to code we already have come before any new source.** | Several gaps that look like missing data are bugs (§1.2). They cost days, not weeks, and every new source depends on them. |

---

## 1. What exists today

### 1.1 By bill-of-materials line

| BOM line | What the graph holds | Source | Tier | Main gap |
|---|---|---|---|---|
| Contract manufacturer / filler | 119,120 Udyam firms (cosmetics, trade and packaging NIC codes), CDSCO loan-licence pairs, state-FDA floor of 544 | s17b, s01, s02 | 1 | `PERMITS` is empty (0012). `THIRD_PARTY_MANUFACTURED_BY` has 0 edges (0015). |
| Ingredients | 764 `SUPPLIES` edges from **53 companies**; one host (anmol.org) is 45% of them | s14 → s14b | 3 | Almost nothing; see §1.2 |
| Chemicals (base, surfactant, aroma, oils) | **None.** No chemical NIC code is in the Udyam filter. | — | — | The whole layer |
| Primary and secondary packaging | 60,087 `SUPPLIES_PACKAGING` edges | s17b | 1 (registry activity) | Coarse: 10 component types, `volume_ml` never set. 24% of the edges come from generic metalwork codes. |
| Test lab | NABL scope register | s11 | 1 | Fine for now |
| Price, MOQ, lead time | **None** | — | — | — |
| RFQ, quote, outcome | **None.** The compare tray lives in memory. | — | — | — |

Empty ontology slots that adjacent data would fill: `TradeName`, `MAPS_TO_INCI`,
`DISTRIBUTES_FOR`, `TradeFlow`/`IMPORTED`, `THIRD_PARTY_MANUFACTURED_BY`, `CERTIFIED_AS`,
and the derived `CAN_FILL`.

### 1.2 Gaps that are bugs, not missing data

- **Udyam yield is 1.6%, not the ~5% in 0006.** The sweep made 12,271 requests over 3,264
  of 4,842 cluster pincodes, scanned about 7.3M records and kept 119,120. 165 pincodes hit the
  10k window cap. Kerala and West Bengal were never swept, Assam is unswept and Tamil Nadu is
  partial. **No raw page was landed:** `raw_path` is empty and there is no `fetch_manifest`.
  Non-matching rows were discarded.
- **The packaging NIC set has precision bugs.** Checked against Udyam's own description
  of each code (decisions/0027). 23103 (hollow glass: bottles, jars) and 25992 (tins and
  cans) are missing. 23109, which fed every `glass_container` edge (3,110), is
  construction, optical and watch glass. 25910 (forging and stamping) and 25920 (machining and
  coating) are in, and between them make 14,502 edges (24.2%). 23101 (glass in primary or
  semi-manufactured form, not containers) adds 2,151 more. 18115 (printing on plastic,
  glass or metal) is missing.
- **Kept rows already declare chemical codes:** 46497 (1,320), 21003 (1,140), 20299 (540),
  20119 (462), 20293 (377), 46693 (369), 46691 (219), 23103 (166). These are firms we kept
  for a cosmetics code, so the counts show the overlap. They say nothing about the size of
  the chemicals population.
- **S14b drops glycerin twice.** GLYCERIN is in the `AMBIGUOUS` list (`s14b_supplies.py:68`),
  and the single-word 9-character minimum (`:52`, `:113`) drops 571 more INCI names,
  including SQUALANE, CARBOMER, LECITHIN and CAFFEINE. The matcher also skips 2,558 names that
  contain parentheses and uses no synonyms, although CosIng's payload carries them. The
  "≥3 ingredients" rule is applied per host, while the README says per page.
- **S14 never follows links.** It tries 12 fixed paths per site, capped at 6. Only 20
  `/products` URLs have ever been fetched.
- **The resolver loads `website` but never uses it.** CHEMICALS, ORGANICS and SPECIALITY
  count as generic name tokens, so chemical-firm names collide.
- **`derive.py:131` reads `nic_codes_cosmetics`.** National rows don't carry that field, so
  20,361 core-cosmetics firms have no `SERVES_CATEGORY` edge.
- **The pincode reference throws away GeoNames latitude and longitude.**
  `adapters/s15_reference.py` lands IN.zip and keeps only pincode, district and state.
  "Supplier near me" needs nothing new, just those two columns.
- **The router has no "supplier of X near Y".** It has 14 templates;
  `suppliers_of_ingredient` takes no location, and the empty-packaging message still says
  "Gujarat only".
- **Contacts:** 975 companies have an email and 473 a phone, and the card shows neither.
  That matters for the contact step of the loop (§4.7).
- **Hygiene, found in passing:**
  - `cache/run_full.sh` runs `TRUNCATE crosswalk`, which breaks the append-only rule.
  - `make ingest SOURCE=s17` prefix-matches two adapters.
  - The trust figures in `latest.json` are stale.

### 1.3 What msme/ gives us for free

- The material-mediated join: NIC → {outputs, inputs} material slugs, with `SELLS_TO`
  derived at query time and never stored.
- The pincode → district → state fallback.
- The bulk-outside-the-graph pattern (Parquet/DuckDB, promote selectively).
- **1,161 NIC codes with cached LLM output/input enrichments**, including 20xxx and 466xx.
  They can be re-snapped onto a cosmetics vocabulary at $0. msme's vocabulary is
  textile-only and maps "chemicals" to `dyes`, so the codes are reusable and the
  vocabulary is not.

---

## 2. Framing: the procurement loop is a bill of materials

A brand's request ("serum, 10k units, 30 days") breaks into lines:
- actives and base ingredients, and fragrance;
- the primary pack (bottle, dropper, pump, jar or tube) and its closure;
- the secondary pack (carton, label);
- a filler or contract manufacturer;
- a test lab.

For every line the loop needs five layers of data. Every source below sits on this grid:

| Layer | Question | Who can supply it |
|---|---|---|
| **A. Exists** | Who supplies this line, near me? | Registries (Udyam), associations, fairs |
| **B. Sells** | What exactly do they sell: material, grade, pack format? | Company sites, certifier registers, regulator product tables |
| **C. May / can** | Is there regulator or certifier evidence that they make it, at what capacity? | Environmental clearances, pollution-board consents, certifiers |
| **D. Terms** | Price, MOQ, lead time | **First-party only.** The public data is benchmarks. |
| **E. Outcome** | Did a buyer transact, and how did it go? | **First-party only.** Buyer confirmation, e-invoice QR, consented GSTR-2B. |

**What this means:** public sources can fill A broadly, B narrowly and C for a few
thousand firms at most. D and E come only from sitting in the transaction, which the
product-defense session and the PRD both point at (the "Moglix trap" is trying to
scrape D instead). So the RFQ rail is planned as a data source (§4.7), not only as a
feature.

---

## 3. Questioning the premise

**3.1 Is "add chemicals data" the right ask? Partly.** A buyer never wants "chemical
companies". They want a supplier for one line of their BOM. Scoping by BOM keeps the
graph answerable. Of Udyam's chemical codes, the ones a cosmetics buyer cares about are
the aroma, surfactant, essential-oil and oleochemical classes. Generic codes ("organic
chemicals n.e.c.") would multiply the noise.

**3.2 Will a better directory solve it? No.** We probed 14 directories and associations.
Most publish names only, or gate details behind membership (CHEMEXCIL, PLEXCONCIL,
CAPEXIL, FAFAI). The two rich commercial directories, Chemical Weekly Buyers' Guide and
the marketplaces, are paid or forbid scraping.

**3.3 Is product-level evidence available at all? Yes, from an unexpected place.**
Environmental clearances and pollution-board consents are granted per product with a
capacity, and committee minutes publish those tables. For a chemical manufacturer above
the size thresholds, this is the only tier-1 statement of what it makes.

**3.4 Can public data close the loop? No, and it shouldn't try.** Price, MOQ and lead
time are negotiated. Every route that claims to publish them is a marketplace (terms
forbid scraping) or a paid estimate. The loop itself is the source.

---

## 4. Source register

Columns:
- **L** — the layer from §2.
- **Tier** — what the source may claim.
- **Verdict** — G = buildable now; A = needs a key, money, a partnership, a manual step or
  legal review; R = not feasible.
- **Fallback** — where execution goes if the build fails.

Proposed card ids (`s20`+) are provisional.

### 4.1 Ingredients and chemicals

| Source | L | Tier | Verdict | Probe evidence | What it may claim / `do_not` | Fallback |
|---|---|---|---|---|---|---|
| **Udyam re-sweep with chemical NIC codes** (s17b, widened) | A | 1 for existence and declared activity; **4** for any material it implies | **G** | Already built. Resource `8b68ae56-…`, `filters[Pincode]`, 1.2 s/request, no 429s in the first sweep. A live re-check with our own key is still pending: the shared sample key was rate-limited. | Existence and declared NIC only. A NIC code never proves a firm makes glycerin. `do_not`: don't promote a generic chemical code into the answerable graph without BOM evidence (decision 1). | The landed 119k rows plus MCA; or the co-occurring codes in rows already kept |
| **COSMOS approved and certified raw materials**, `?country=IN` (s20) | B, C | 1 for "this material is COSMOS approved/certified" (certifier register, as NABL); 2 for "company sells it" | **G** | 200, server-rendered, robots.txt allows. 10 pages approved and 3 pages certified for India. Row: *Ajwain Essential Oil · Trachyspermum Ammi Oil · AVT Natural Products · ECOCERT GREENLIFE*. Others seen: Godavari Biorefineries (ethanol), Kingvish. | Fills `TradeName` → `MAPS_TO_INCI` and `SUPPLIES`. `do_not`: it is a name-only source, so per 0010 a company match needs name and location confirmation or goes to review. | Ecocert's own listings; supplier self-declaration |
| **Environmental-clearance minutes: EAC Industry-3 and state SEAC** (s21) | B, C | 1 | **G** for archived PDFs; **A** for discovering new ones | 25th EAC minutes, 94 pp. pdfplumber extracts product tables cleanly (*PCl3 · 10,000 · 20,000 · 30,000 TPA*). Newer PARIVESH letters sit at predictable URLs but push products into an annexed Form-1, sometimes scanned. | "This unit was cleared to make product P at capacity C, as of date D." Capacity is a ceiling, not output. `do_not`: don't read a clearance as proof of current production. Skews to larger synthetic-organics units. | MPCB/GPCB documents; company-republished consent orders |
| **Udyam co-occurrence pre-estimate** | A | 4 | **G**, $0 | Counts in §1.2 | Sizing input for the re-sweep only. Never shown to users. | — |
| **CosIng synonyms + PubChem synonyms + CAS** (s06 widened, s32) | Vocabulary | 1 for CosIng; 3 for PubChem | **G** | CosIng's payload already carries synonyms. PubChem PUG REST: Glycerine → CID 753, CAS 56-81-5, but the list contains noise ("Moon", "Optim"). | Synonym edges are used for matching, never shown as facts. `do_not`: no PubChem synonym without a CAS or INCI cross-check; drop entries under 4 characters and dictionary words. | CosIng alone (still fixes glycerin) |
| **SEA of India member lists by product** (s26) | A, B | 2 | **G** | 200, robots.txt allows. About 20 product categories (castor derivatives, solvent extraction …). Name, city and state only. | `MEMBER_OF` plus a coarse material hint. Castor and oleochemical makers feed glycerin and fatty-acid lines. | Udyam 10402/10409 |
| **Distributor principals pages and press releases** (s27, hand-curated seed) | B | 2; confirmed from both sides → stronger | **G** (manual) | No principal publishes an India locator per product line (10 probed: BASF, Croda, Clariant, Dow, Evonik, Ashland, Lubrizol, dsm-firmenich, Givaudan, Symrise). Press releases give pairs: Evonik→Azelis, Lonza→Anshul, Ashland→DKSH, Clariant→DKSH, Lubrizol→Arihant Innochem. Anshul's site lists Cabot, Mibelle and SNF. | Fills `DISTRIBUTES_FOR` with date and scope. `do_not`: a press release from 2013 is not current; show its date. | Ask distributors in the RFQ flow |
| **Cosmoprof India 2026 exhibitor PDF** (s08, new edition) | A | 2 | **G**, low yield for ingredients | 98 KB PDF, ~370 rows, 11 "Ingredients & Raw Materials", ~86 packaging, ~48 OEM/ODM | `EXHIBITED_AT` plus a category hint | in-cosmetics Asia (A) |
| in-cosmetics Asia exhibitors | A | 2 | **A** | A sitemap lists ~733 exhibitor URLs (Galaxy, Kumar Organic, Ester India). The directory is a JS client behind an Apigee API. | As s08 | Ask the organiser |
| **Chemical Weekly Buyers' Guide (cwbg.in)** | A, B | 2/3 | **A** (paid licence) | Claims 9,500+ manufacturers, dealers and exporters, with installed capacity and production per item. Login and price unknown. | The best licensable product→manufacturer directory | Request a licence quote; until then, EC minutes plus Udyam |
| GST taxpayer search, goods (HSN) dealt in | B | 1 | **Unverified** | Not probed: it depends on s16, which is blocked. | If it lists HSN codes, it is a tier-1 product signal per GSTIN. | Add it to the s16 vendor due-diligence questions |
| ECHA CHEM (REACH) | C | 1 (EU) | **A** | The dossier API returns no company names. The C&L notifier-name release was due July 2026; not checked. | EU registration via an Only Representative | Skip for now |
| US FDA DMF list | C | 1 (US) | G, but pharma-tilted | 51.5 MB quarterly XLS. Type IV = excipient. | Excipient makers that are export-grade | Skip for now |
| IFRA Standards library | Rules | 2 (industry standard) | G | Per-standard PDFs, robots.txt allows | Fragrance restriction data. **Not a jurisdiction:** never an Indian rule. | Park until fragrance lines matter |
| RSPO supply-chain certificate holders | C | 1 for the certificate | **A** | AJAX shell; CSV export announced but not seen | Palm-derived glycerin and fatty alcohols | Supplier shares its certificate |
| CHEMEXCIL, CAPEXIL, ISCMA, FAFAI, SHEFEXIL, EOAI | — | — | **R** | Member-only, 403 or 502 | — | Udyam + EC minutes |
| Principal product-line locators | — | — | **R** | None exist (above) | — | s27 seed |
| UL Prospector, SpecialChem, Knowde | — | — | **R** | Terms ban it, Cloudflare 403, robots.txt blocks ClaudeBot | — | COSMOS + supplier claims |
| CPHI & PMEC India | — | — | **R** | robots.txt `Disallow: /` | — | Informa partnership |
| BIS Manak licence search for chemicals | — | — | **R** | Login and captcha. Most chemical quality-control orders were withdrawn in Nov 2025, so there is little to find. | — | — |
| PESO, DGFT IEC, DPIIT IEM | — | — | **R** | Not about ingredients, captcha, or aggregates only | — | — |

### 4.2 Packaging

| Source | L | Tier | Verdict | Probe evidence | What it may claim / `do_not` | Fallback |
|---|---|---|---|---|---|---|
| **Udyam packaging NIC set, corrected** (s17b) | A | 1 activity; 4 component | **G**, applied 2026-10-29 | Typed by the registry's own text per code (decisions/0027). Added 23103 (bottles, jars), 25992 (tins and cans), 18115 (printing on plastic, glass or metal). Kept 22201/22202/22203, 17022/17023/17029. Untyped 23101 (sheet glass), 23109 (construction and optical glass), 25910 (forging and stamping) and 25920 (machining and coating). Not added: 18119 (generic screen printing), 17021 (board sold to box makers). | `SUPPLIES_PACKAGING` to a *vocabulary* type (decision 2), still `volume_ml` null | — |
| **Cosmoprof India 2026 exhibitors** (s08) | A, B | 2 | **G** | ~86 "Primary & Secondary Packaging" rows: EPL, Uflex, PGP Glass, Purity Polytubes, Sigma Sprays … | `EXHIBITED_AT` plus a packaging category | PackPlus via the organiser |
| **AIPMA member directory** (s26) | A | 2 | **G**, with a privacy caveat | 200, robots.txt allows at a 3 s delay. Fields: company, address, pincode, category, GSTIN (sometimes), contact person and mobile. The product filter doesn't work. 100+ entries. | Company fields and GSTIN only. `do_not`: store no contact person or mobile (DPDP; see §6). | Udyam |
| AIGMF (glass) | A | 2 | G, thin | 57 manufacturing members and 77 affiliates, names only | `MEMBER_OF` | Udyam 23103 + Cosmoprof |
| **CPCB EPR plastic portal: registered Producers and Brand Owners** | C | 1 | **A** | Angular SPA. The company tables need a POST whose payload is encrypted in the browser. A CPCB filing to the green tribunal counts 62,175 registered PIBOs (Jun 2026). It **cannot** link a brand to its packaging producer: those links sit in private annual returns. | A badge, "registered EPR Producer, category X", not an edge | **RTI to CPCB** for registered Producers by state and category. Replicating the encrypted POST needs legal review first. |
| BIS first-licence report | C | 1 | G, low value | 200, 2,032 rows. Only ~25 look like packaging or resin. The polymer quality-control orders were revoked on 12 Nov 2025. | Licence badge | — |
| PackPlus 2026 exhibitors | A | 2 | **A** | 197 exhibitors, but robots.txt on the host disallows everything | — | Ask RX India for an export |
| PlastIndia 2026 | A | 2 | **A** | 1,844 exhibitors behind an exhibitor login. A third party sells the e-catalogue. | — | Buy or request the catalogue |
| Pamex 2026, Labelexpo India 2026 (29 Oct–1 Nov) | A | 2 | **A** | A dynamic table with 0 rows in the raw page; a Cloudflare challenge | — | Collect the catalogue at the show |
| PLEXCONCIL | A | 2 | **A** | Member .xlsx files return 403 | — | Ask the council for its product-panel lists |
| FCBM, OPPI, LMAI, IIP, IFCMA, aerosol association | — | — | **R** | Empty pages, no directory, 403, or the domain doesn't resolve | — | Fairs + Udyam |
| Legal Metrology packer registrations | — | — | **R** | No public list at central or state level | — | Brand sites (§4.3) |
| Open Beauty Facts packaging fields | — | — | **R** | 5 of 200 India products sampled carry packaging data | Keep OBF for category and ingredients | — |
| IndiaPack/Pacprocess, Printpack | — | — | **R** | Lists from 2019 and 2022, mostly machinery | — | — |

### 4.3 Contract manufacturers, fillers and brands

| Source | L | Tier | Verdict | Probe evidence | What it may claim / `do_not` | Fallback |
|---|---|---|---|---|---|---|
| **Goa drug regulator cosmetics lists: loan and own manufacturers** (s02, widened) | A, C | **1** | **G** | Two PDFs dated 13 Oct 2026. Line: *"Opella Healthcare C/o Encube Ethicals, Madkaim, Ponda"*. Small: 3 loan and 6 own. | `THIRD_PARTY_MANUFACTURED_BY` **with direction**, because the line format is "Brand C/o Manufacturer". It is the first tier-1 source that resolves 0002's direction problem for its rows. | **RTI** to the Maharashtra, Gujarat, Himachal and Uttarakhand drug regulators for the same lists. That is where the volume is. |
| **D2C brand websites ("Manufactured by")** (s24) | B, C | 2 | **G** | Shopify `products.json` returns 200 for beminimalist.co, thedermaco.com, plumgoodness.com and foxtale.in, and robots.txt allows it. Foxtale names *Anaika Industry, Plot 52, Mandvi Village, Virar East*. The Derma Co names only the marketer, Honasa. | `THIRD_PARTY_MANUFACTURED_BY` brand→manufacturer, tier 2. It also gives pack sizes and product types for BOM templates. This doesn't contradict 0015: labels must name the manufacturer, while SEBI filings needn't. `do_not`: a "Marketed by" line is not a manufacturer. | Fallbacks: tier-1 state lists above; GS1 DataKart (paid). |
| statedrugs.gov.in licence verification | C | 1 | **A** (manual) | One licence at a time, captcha plus OTP | Verifies a licence number that a supplier gives inside the RFQ flow, by hand | — |
| CDSCO import registrations (COS-2) | A | 1 | **A/R** | Only a 2013–17 list and cancellations are public | Importer of record for imported brands | RTI to the CDSCO Cosmetics Division |
| Amazon.in / Nykaa "Manufactured by" | — | — | **R** | Amazon's terms ban data mining. Nykaa returns 403 even for robots.txt. | — | Brand sites |

### 4.4 Certification and permission (layer C, cross-cutting)

| Source | Tier | Verdict | Notes |
|---|---|---|---|
| **GPCB XGN consent register** (s22) | 1 | **G** for a "holds a valid consent" flag and a feed of new grants; **R** for products | Public. Search is a form POST with a CSRF token. 628 rows in the default view. Row: *Alembic Pharmaceuticals · CTE-Amendment · granted 25/10/2026 · CTE-162881 · Vadodara*. Unreachable from the packaging agent's network on 2026-10-26, so availability is intermittent. |
| **MPCB Consent Appraisal Committee minutes** (s22) | 1 | **G** | Text PDFs. The 6th CAC (14 Aug 2025) has 103 pp, with firm, address, decision and product at MT/month. Consent copies themselves sit behind a mobile OTP: manual or RTI only. |
| **ZED-certified MSMEs** (s23) | 1 (MSME ministry is the issuer) | **G/A** | Enterprise name, NIC-2 sector, unit address with pincode, state, size, Bronze/Silver/Gold. The sector filter is a POST with a CSRF token. ZED requires Udyam registration, so it joins on name and pincode. Bronze is weak evidence. |
| IAF CertSearch (ISO 9001/22716/15378) | 1 for the certificate | **A** (paid) | Already in TRUST_PLAN. Billed per exact-match credit; "Basic 499" includes 150 credits a year. Run it lazily on shortlisted suppliers, never in bulk. |
| BRCGS, FSC | 1 | **A** | One certificate at a time, so use it inside the RFQ flow |
| ICC Responsible Care list | 2 | G, minor | A trust flag for chemical firms |
| EcoVadis | — | **R** | No public lookup. A supplier can share its scorecard. |

### 4.5 Terms: price benchmarks and logistics (layer D, benchmarks only)

| Source | Verdict | Use |
|---|---|---|
| **tradestat EIDB** (s28) | **G** | HS-8 import value and quantity by month, so unit value = value ÷ quantity. Form POST with a CSRF token, no captcha, robots.txt allows. Starter list, to confirm against ITC-HS: glycerol 2905.45 and 1520; fatty alcohols 3823.70; anionic surfactants 3402.39; white oil 2710.19; petrolatum 2712.10; plastic bottles 3923.30; closures 3923.50; pouches 3923.21/29; glass bottles and jars 7010.90; scent sprays 9616.10; aluminium tubes 7612.10; cans 7612.90/7310.29; cartons 4819.10/20; labels 4821.10; laminated foil 7607.20. |
| **WPI, base 2022-23** (s29) | **G** | Monthly item indices for trend, not price. Ingredient items: fatty acid, organic surface-active agents, castor oil, ethyl alcohol, menthol, essential oils. Packaging items: plastic bottles, closures, flexible pouches, labels, laminated paper, corrugated boxes, glass vials, PET/PE/HDPE/PP. There is **no glycerin, petrolatum or white-oil** item. The download needs a Referer header. |
| **GeoNames pincode latitude/longitude** (s15, widened) | **G** | Already landed. Keep two more columns. Use straight-line distance first. |
| OSRM road distance | G if self-hosted | The public demo is not for production. The Geofabrik India extract is 1.71 GB. Defer until straight-line distance proves too crude. |
| Polymer prices (IOCL, Reliance, GAIL), Intratec, ChemAnalyst, Alchempro | **A** | No official public list. The rest are paid: Intratec glycerol from $348/yr; Alchempro mostly locked. WPI plus tradestat cover the trend. |
| Freight indices (Crisil, Shriram, Delhivery) | **A** | Index or % change only. Real freight quotes come in RFQ responses. |
| Volza, Seair, Eximpedia, Zauba, Panjiva (Indian importer-named) | **R** | Customs Act s.135AA. US import manifests that name Indian shippers are a lawful tier-3 alternative, with no prices. |
| IndiaMART, TradeIndia, ExportersIndia, JustDial | **R** | IndiaMART's terms ban systematic retrieval by robots or manually without written permission; robots.txt blocks ClaudeBot. Its only API is the seller-side Lead Manager. TradeIndia blocks search and supplier pages. |

### 4.6 Identity and contact (supports every layer)

| Source | Verdict | Notes |
|---|---|---|
| Company website/domain as a resolver key | **G** (code) | Already loaded and unused. It is the join key for COSMOS, brand sites and distributor pages, which give names but no IDs. |
| GSTIN from AIPMA and supplier claims | **G** | Feeds TRUST_PLAN rung 0 straight away |
| e-invoice IRP public keys | **G** | Live page with production and sandbox certificates. A signed-QR check works offline. Each private IRP likely has its own key; not confirmed. Layer E, per TRUST_PLAN L6. |

### 4.7 The first-party RFQ rail (s31), the only source of layers D and E

PRD §9.1 already names the objects. This plan adds where each lives and what tier it carries:

| Object | Store | Tier / provenance | Visibility |
|---|---|---|---|
| `buyer_request` (BOM lines, quantity, date needed, TEMQ evidence) | Postgres | first-party, buyer-declared | Private to the buyer |
| `shortlist`, `rfq` (one per BOM line × supplier) | Postgres | first-party | Private |
| `rfq_response` (quote, MOQ, lead time, sample terms, freight) | Postgres | 2 (supplier-declared) | The buyer sees their own quotes. Aggregates go out only with ≥3 suppliers and ≥2 buyers per material. |
| response time, response rate | Postgres | `provenance_class: first_party` (measured by us) | Shown as a supplier attribute once n ≥ 5 RFQs |
| `outcome` (order placed, delivered on time, quality OK) | Postgres | buyer-confirmed → e-invoice QR verified → consented GSTR-2B (TRUST_PLAN) | Private by default; a "transacted with" signal only with ≥2 distinct buyers |
| `interaction_event` | Postgres | append-only log | Internal |

No outcome becomes a graph edge until a decision record says how. The label `Event` is
taken by trade fairs, so these tables never use it.

**Answer to the PRD's open question** ("the first closed-loop event we can reliably
track"): **an RFQ response received through our rail.** It is fully observable by us,
needs nobody's consent beyond the two parties to the RFQ, and gives D-layer data on day one. Order and
delivery come later, through buyer confirmation, then QR verification.

---

## 5. Decisions in detail

Each becomes a decision record after review. Numbers are provisional.

**D1 → 0017 Scope is the cosmetics BOM.**
- Chemical, oleochemical and packaging firms are landed and staged in Postgres in full.
- They are promoted to the answerable graph only when one of these holds:
  - (a) a source links them to a BOM material (COSMOS, EC/consent product table, company
    site, distributor seed, RFQ response); or
  - (b) they hold a code the graph already keeps, or one of two curated supplier codes
    (20118 aroma chemicals, 20293 essential oils and modified oils and fats). 20233
    was dropped from this list. It is detergent manufacture, it is already kept as part
    of 2023x, and `derive.py` already refuses to read it as cosmetics (0017).
- Generic codes (20119, 20299, 46691, 46693) stay staged.
- This mirrors 0001 for pharma and msme's bulk/graph split. It is reversible: staging
  keeps every row.

**D2 → 0018 Material vocabulary.**
- `Ingredient` stays keyed on `inci_name`. `TradeName` + `MAPS_TO_INCI` are filled from
  COSMOS, distributor pages and supplier claims. Synonyms (CosIng, then filtered PubChem)
  and CAS live on the node for matching.
- A curated table maps the top ~150 BOM materials to HS-6/8 codes (ECICS look-ups by CAS
  where needed) and to the NIC codes that produce them.
- Packaging gets a controlled vocabulary seed, `ontology/packaging_vocab.yaml`. Each
  `type.material` slug (e.g. `bottle.pet`, `bottle.glass`, `jar.pp`, `pump.lotion`,
  `pump.airless`, `dropper.glass`, `tube.lami`, `tube.alu`, `cap.pp`, `carton.folding`,
  `label.self_adhesive`, `can.aerosol`) carries its NIC and HS codes.
  `PackagingComponent.type` comes from this list only.
- The cached msme enrichments are re-snapped onto both vocabularies.

**D3 → amends 0006. The Udyam re-sweep lands raw pages.**
- Every page goes through `lib.land()` (gzip) with a `fetch_manifest` row. The NIC filter
  then runs downstream, so the next widening needs no re-sweep.
- Sweep the 1,578 unswept pincodes as well.
- Record the measured yield (1.6%) in 0006.
- Estimate: ~12,300 requests for the swept pincodes plus ~5,900 for the rest (at ~3.8 per
  pincode), so ~18k requests, about 6–7 hours at 1.2 s each.
- Storage: ~3.6 GB uncompressed before gzip.
- The 165 over-cap pincodes stay a known hole. The API has no NIC filter, so nothing
  closes it.

**D4 → 0019 Hierarchy of evidence that a firm sells a product.**
1. Tier 1: EC/SEAC tables, pollution-board consent products, COSMOS certification, the
   Goa licence lists.
2. Tier 2: company site, brand label, association product list, distributor
   principal page, RFQ response.
3. Tier 3: exhibitor category, scraped listings.
4. Tier 4: NIC → material inference.

A card shows the strongest level held for each material. Tier 4 is labelled "likely" and
never answers "who makes X" alone.

**D5 → 0020 A clearance or consent is a ceiling, not production.** It is a `Licence`
node (`kind: env_clearance | pcb_consent`) with a new `PERMITTED_TO_MAKE` edge to
`Ingredient`. That edge exists only for unambiguous INCI matches, per 0011. Other products
stay in Postgres `licensed_product`. `PERMITS` (Licence → ProductCategory, cosmetics) is
untouched, and 0012 still holds.

**D6 → 0021 Who-makes-for-whom.**
- Goa-style lists are tier 1 and directional.
- Brand labels are tier 2 and directional.
- Both write `THIRD_PARTY_MANUFACTURED_BY` with `direction_known: true`.
- CDSCO `LOAN_LICENCE_WITH` stays undirected (0002) unless one of these confirms it.
- 0015's finding is restated rather than reversed: filings don't disclose, labels do.

**D7 → 0022, and s10's card. No marketplace scraping.**
- IndiaMART, TradeIndia, Amazon and Nykaa stay `not_built`. The `do_not` now cites their
  terms and robots.txt.
- The build spec once listed IndiaMART as "PoC only". That is withdrawn.
- The same data arrives lawfully through claimed supplier profiles and RFQ responses (§4.7).

**D8 → 0023 No importer-named Indian customs data.**
- Use aggregates only (tradestat, DGCI&S).
- US manifests naming Indian shippers are allowed at tier 3, after a check of the
  vendor's terms.

**D9 → 0024 Benchmarks.**
- `price_benchmark(hs8, month, value_inr, qty, unit, unit_value, source)` and
  `wpi_item(item_code, month, index)` live in Postgres and never in the graph.
- They are displayed as "import unit value, month, n shipments not known" or "price index".
- They are never displayed as a price, a quote, or next to a supplier name as if the
  supplier charges it.

**D10 → 0025 The RFQ rail is a source.** It covers the tables in §4.7, its own card, and
the visibility thresholds. Quotes are tier 2. Response metrics are first-party. Outcomes
follow TRUST_PLAN's evidence ladder.

**D11 → 0026 BOM templates.**
- A curated seed maps a product category to its function classes and lines. For example,
  a serum is: humectant, emollient, active, preservative, thickener, fragrance; dropper
  bottle, closure, carton, label; filler; stability + challenge test.
- Each line points to the vocabulary (D2) and to a relevant standard where one exists.
- Templates are tier 4 reference data. They structure the RFQ pack. They never claim
  anything about a supplier.

**D12 → amends 0007. Identity for name-only sources.**
- Website/domain becomes a resolver key, at the same strength as phone.
- ~~Widen the generic-token list.~~ **Not done.** CHEMICALS → CHEM, ORGANICS and
  SPECIALITY are already generic. Widening the list further would merge "MEGHMANI DYES
  INTERMEDIATES" with "MEGHMANI ORGANICS", which are distinct CINs (0007 amendment).
- Name-only sources (COSMOS, SEA, AIGMF, exhibitors) need a second signal (pincode,
  domain or GSTIN) or go to review (0007, 0010).

**D13 → 0028 Geography.**
- Supplier search ranks by straight-line pincode distance, not cluster membership.
- The chemical and packaging belts (Vapi, Ankleshwar, Bharuch, Dahej, Tarapur, Thane,
  Raigad, Silvassa) mostly sit inside existing clusters (`GJ_AHMEDABAD_VAPI` covers
  prefixes 392/393/394/396; `MH_PALGHAR_TARAPUR` covers Thane and Raigad).
- Clusters stay as a lens. They are not a filter.

---

## 6. Legal and personal data

- **Terms and robots.txt are a hard gate.** Record the robots.txt and terms URL checked,
  with the date, in the source card's `acquisition`. Anything that disallows us is red,
  whatever the value of the data.
- **No captcha, OTP or login automation, ever.** MPCB consent copies, statedrugs licence
  verification and DGFT IEC stay manual, used only inside an RFQ where a supplier gives us
  the number.
- **Encrypted-payload endpoints (the CPCB EPR portal):** don't replicate them without
  legal review. RTI is the default.
- **DPDP:**
  - AIPMA, BIS and some PARIVESH letters publish named individuals with mobiles and
    emails. Store enterprise name, address, GSTIN and provenance. Drop personal
    names, mobiles and personal emails at the adapter, in line with the "no `Person` in
    Neo4j" rule.
  - Whether the publicly-available exemption covers data an association published about
    a person is unsettled. Get counsel before relying on it.
  - Proprietorship GSTINs follow `public_gstin()`.
- **Customs Act s.135AA:** see D8.
- **Defamation:** no negative supplier signal (late response, lost RFQ) is ever public.
  Only positive, consented, thresholded aggregates are shown, per TRUST_PLAN §7.4.

---

## 7. Execution order

Each phase ends with `make test`, `make catalog`, `make catalog-check` and an updated
coverage number. Every new source's card and `do_not` block is written before its adapter.

| Phase | Work | Cost | Done when |
|---|---|---|---|
| **0. Fix what we have** | (1) `derive.py:131` key bug. (2) S14b: drop GLYCERIN from `AMBIGUOUS` (with the router and `index_docs` side-effects), replace the 9-character rule with a length + dictionary check, handle parentheses, add CosIng synonyms, reconcile per-page and per-host. (3) S14 follows links to product pages. (4) Resolver website key and generic tokens (D12). (5) Keep GeoNames latitude/longitude, plus a "supplier of X near Y" router template. (6) Show email and phone on the card. (7) Hygiene: `run_full.sh` TRUNCATE, `make ingest` prefix match, stale `latest.json`. (8) Apply the packaging NIC precision fix to rows already held: untype the 25910/25920/23101 edges, and use 23103 where it co-occurs. | ₹0, ~3–4 days | `SERVES_CATEGORY` +~20k. S14b matches glycerin. A golden query "glycerin suppliers near 380001" returns tier-labelled rows. 196+ tests pass. |
| **1. Vocabulary + Udyam re-sweep** | D2 vocab seeds and the HS/NIC map. Re-snap the msme enrichments. Udyam re-sweep with landing and the widened NIC set (D3). Chemicals staging table. D1 promotion rule. | ₹0; ~7 h unattended API time plus ~2 days of code | `fetch_manifest` row count = requests. Yield measured. Chemical firms staged, and promoted ones carry a D1 reason. |
| **2. Tier-1 product evidence** | COSMOS (s20). MPCB CAC + GPCB register (s22). EAC/SEAC archive (s21, archived PDFs first). ZED (s23). `PERMITTED_TO_MAKE` (D5). | ₹0; ~1–1.5 weeks | Every edge from these sources has tier 1 and a document URL. A golden query asserts that `PERMITTED_TO_MAKE` never answers a cosmetics `PERMITS` question. |
| **3. Who-makes-for-whom and suppliers by name** | Goa lists (s02). Brand sites (s24). Cosmoprof 2026 in s08. SEA/AIPMA/AIGMF (s26, company fields only). Distributor seed (s27). BOM templates (D11). | ₹0; ~1 week | `THIRD_PARTY_MANUFACTURED_BY` > 0 with `direction_known: true`. `DISTRIBUTES_FOR` > 0. No `Person` data. |
| **4. Benchmarks** | tradestat (s28), WPI (s29), for the HS list in §4.5 | ₹0; ~2–3 days | Unit values for ≥20 HS-8 codes × 24 months. Nothing in Neo4j. |
| **5. RFQ rail as a source** | §4.7 tables, s31 card, visibility thresholds. This is PRD P3, where the build overlaps with the product team's own work. | Engineering time | First RFQ response recorded end-to-end with its response time |
| **In parallel, from week 1** | RTI: CPCB EPR Producers; MH/GJ/HP/UK drug regulators' cosmetics loan-licence lists; CDSCO COS-2 holders. Quotes: CWBG licence, IAF CertSearch plan. Legal: DPDP exemption, the EPR encrypted endpoint. Ask RX India for PackPlus data; collect Labelexpo (29 Oct–1 Nov) catalogues. | RTI fees are nominal; replies take ~30 days | Each reply either becomes a source card or a red verdict with its reason |

### Phase 0 status (2026-10-30)

Done and verified: 34/34 golden queries, 214/214 tests and 43/43 demo questions pass.

- **(1) Category key fix.** `SERVES_CATEGORY` now reaches 21,046 companies, up from 1,259.
- **(2) S14b matcher.** `pipeline/materials.py` (synonyms and CAS) took the matcher from
  804 claims over 53 suppliers to 2,471 over 238 on the old pages. On the crawled
  catalogue pages it needed four more rules (0011, amended 2026-10-30): a name must be a
  catalogue entry, not a mention in a sentence; negation and source words, substituent
  prefixes and ester/salt suffixes block it; and sites that sell finished or private-label
  cosmetics are dropped. Now 8,868 claims over 486 suppliers (6,542 for cosmetic
  ingredients), GLYCERIN 55 suppliers, measured ~83% precise on 100 uniform samples.
- **(3) Link-following.** Done: 3,031 catalogue pages landed from 1,127 sites, taking
  S14 to 7,339 pages and 1.1 GB.
- **(4) Resolver.** Website is now a contact key. The generic-token list was not widened
  (0007).
- **(5) Location.** Pincode centroids, plus the `ingredient_suppliers_near` and
  `packaging_suppliers_near` templates.
- **(7) Hygiene.** Except the stale `latest.json`, which `make catalog` refreshes.
- **(6) Email and phone on the card.** Business contacts only (0029): nothing for a
  proprietorship, phone plus role mailbox for a legal entity, a role mailbox only where
  the form is unknown. 810 firms show one; the rest stay in Postgres.
- **(8) Packaging codes.** Retyped per 0027: `SUPPLIES_PACKAGING` went from 60,087 to
  40,962 edges. About 18,000 firms now have no in-scope code. They stay in the graph
  until Phase 1's promotion rule.

Not done:

- **Stage 7 re-index.** The document linker now uses the shared vocabulary, but
  `make index` has not been re-run, so `MENTIONS` still reflects the old needle rules.

## 8. If a source fails: where to loop

| If this fails… | …do this next |
|---|---|
| Our Udyam key is rate-limited or the API changed | Run the re-sweep with a lower request rate over several days; meanwhile promote the chemical codes already co-occurring in the 119k landed rows |
| EAC/SEAC tables turn out to be scanned | OCR only the tables we need, not whole documents; MPCB CAC minutes (text PDFs) first |
| PARIVESH new-document discovery fails | Archived minutes cover the past; the company's own republished consent orders cover the rest |
| GPCB register is unreachable (it was on 2026-10-26) | Retry on a schedule; MPCB carries the Maharashtra half |
| COSMOS India pages change or are too thin | Ecocert listings; supplier-claimed certification checked inside the RFQ |
| Brand sites don't name the manufacturer | Goa lists + RTI lists (tier 1); ask the brand in the claimed-profile flow |
| RTI replies are refused or slow | Escalate to first appeal; meanwhile tier-2 sources stand |
| CWBG is too expensive | EC minutes + Udyam + COSMOS stay the backbone; CWBG is an accelerator, not a dependency |
| tradestat CSRF form changes | The Dataful mirror of the same HS-8 data |
| A directory forbids crawling after we start | Stop, mark the card `blocked`, keep only what was landed under the old terms if counsel agrees |

## 9. Decisions only Sarvesh can make

1. **Scope:** a BOM-scoped widening inside the cosmetics graph (D1, recommended), or a
   separate chemicals/packaging graph that the cosmetics graph queries.
2. **Money:** approve asking for a CWBG licence quote and an IAF CertSearch plan. Nothing
   in phases 0–4 needs either.
3. **Marketplaces:** confirm D7, withdrawing the build spec's "IndiaMART PoC".
4. **RTI:** who files, and in whose name. Replies are public-record and can be published.
5. **Counsel:** the DPDP association-directory question and the EPR encrypted endpoint.
6. **Phase 5 timing:** whether the RFQ-rail tables are built now in this repo or wait
   for the product build.

## 10. Verification

- **Every source:** its card passes `make catalog-check`. Its `do_not` entries that can be
  enforced are golden queries.
- **The glycerin test, end to end.** The 2026-10-08 question is re-asked after each phase,
  and each answer shows its tier:
  - after phase 0: S14b sites;
  - after phase 1: staged and promoted Udyam firms with 20293 or 20119;
  - after phase 2: EC/consent product rows and COSMOS;
  - after phase 4: a tradestat 2905.45 benchmark, labelled as one.
- **New golden queries:**
  - `no_permitted_to_make_answers_permits`
  - `no_tier4_alone_answers_who_makes`
  - `no_price_benchmark_in_graph`
  - `no_contact_person_from_associations`
  - `third_party_manufactured_by_has_direction`
- **Coverage (`make coverage`) reports these separately:**
  - BOM lines with ≥1 tier-1/2 supplier within 300 km of each cluster centroid;
  - the share of promoted chemical firms carrying a D1 reason.

## 11. Not verified, carried forward

- The Udyam API live with our own key (the shared sample key was rate-limited).
- Whether GST taxpayer search lists the HSN codes a business deals in.
- CPCB EPR company-table columns and export (the portal refused the browser).
- ECHA's notifier names (due July 2026); RSPO's CSV export.
- Whether PET resin was in the revoked quality-control orders.
- NIC 2013x (polymers, carbomers) and 1920x (white oil, petrolatum) as supplier codes.
- Cosmetics loan-licence lists from any state other than Goa.
- The DPDP Rules' phased obligation dates.
- The private IRPs' e-invoice public-key URLs.

## Sources (probed 2026-10-25/26)

Regulators and registries:
- environmentclearance.nic.in (25th EAC minutes)
- parivesh.nic.in certificates
- mpcb.gov.in (6th CAC minutes, 14 Aug 2025)
- ecmpcb.in/cms
- gpcbxgn.gujarat.gov.in/Public/ConsentRegistration
- zed.msme.gov.in/zed-certified-msmes
- data.gov.in (Udyam resource 8b68ae56-…)
- dfda.goa.gov.in/manufacturers
- statedrugs.gov.in
- cdsco.gov.in (Cosmetics)
- eprplastic.cpcb.gov.in
- manakonline.in
- tradestat.commerce.gov.in/eidb
- eaindustry.nic.in/download_data_2223.asp
- einvoice1.gst.gov.in/Others/Publickeys
- download.geonames.org/export/zip/IN.zip

Certifiers:
- cosmos-standard.org (approved and certified raw materials, `?country=IN`)
- iafcertsearch.org
- RSPO
- BRCGS
- FSC
- IFRA standards library

Associations and fairs:
- seaofindia.com
- aipma.net
- aigmf
- chemexcil.in
- cosmoprofindia.com (exhibitor list 2026)
- in-cosmetics Asia sitemap
- exhibitors.cphi.com (robots.txt)
- RX PackPlus 2026
- PlastIndia 2026 post-show release

Commercial:
- cwbg.in
- indiamart.com/terms-of-use
- UL Prospector terms
- Intratec glycerol
- Alchempro
- Shopify `products.json` on beminimalist.co, thedermaco.com, plumgoodness.com, foxtale.in

Legal:
- Customs Act s.135AA (Finance Act 2022)
- CPCB compliance report to the green tribunal, OA 993/2024
- QCO revocation, Nov 2025 (chemlinked)
