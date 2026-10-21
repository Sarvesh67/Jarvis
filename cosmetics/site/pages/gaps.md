---
title: Gaps
description: What the data cannot answer, and what that costs.
---

A catalog that lists only what you have is half a catalog. These are the holes, and they
drive more decisions than the figures on the other pages.

```sql missing
select id, name, tier, status, jurisdiction
from catalog.sources
where status <> 'live'
order by tier, id
```

<DataTable data={missing}>
  <Column id=id title="ID"/>
  <Column id=name title="Source" wrap=true/>
  <Column id=tier title="T" align=center/>
  <Column id=status/>
</DataTable>

## The one that matters most

**S2 — state FDA cosmetics licences (COS-8 / COS-9).** This is the actual Indian licence
to manufacture cosmetics, issued by state FDAs rather than by CDSCO.

Until 2026-09-14 this page said "no state publishes a bulk list" as a flat assertion that
had never been checked jurisdiction by jurisdiction. It has now been checked, and the
honest version is more specific and worse.

CDSCO's national licensing system (ONDLS) publishes a live count of active licences by
type and state. Nationally it holds **544** cosmetics manufacturing licences — and that
number is now this page's denominator, replacing a `null`. But it is a floor, not a
population: **14 of 37 jurisdictions report any cosmetics licence at all**, and the 23
reporting zero include Gujarat, Maharashtra, Delhi, Tamil Nadu, Karnataka and Himachal
Pradesh. Those states never onboarded cosmetics licensing; they issue on their own
portals.

Gujarat reports **zero**. Gujarat is this project's spine and its largest cluster. So a
complete harvest of the one national system would still not cover the firms this graph is
mostly made of.

ONDLS does expose a third-party licence verification endpoint, which looked like a route:
scrape the `M.L. No.` a label must legally carry, then verify it at tier 1. It does not
work. Tested with all three cosmetics-regime licence numbers held from S1 and four recent
drug licences as a control — all seven missed. The response field is
`str_ondls_licence_no`: ONDLS verifies against its *own* identifier, not the state-issued
number that CDSCO publishes and that a pack carries. The two numbering systems do not
meet.

The consequence is still visible on this site as a zero. `PERMITS` — the edge connecting a
licence to the product category it authorises — is **empty**, and coverage reports 0%.

That remains deliberate. CDSCO's listing has no category field, so no licence→category
edge can be asserted at tier 1 from anything currently held. An empty edge with an honest
0% is better than a populated edge implying permission the data cannot evidence.

## What inference does instead

A graph that cannot evidence permission can still rank, and until recently this one barely
did. Two tier-4 edges now carry that load, and neither may answer a compliance question —
the `tier4_never_answers_compliance` golden query and the Cypher guard enforce that
mechanically, and a further test asserts that every edge the ontology declares derived is
registered with the guard.

`SERVES_CATEGORY` (from registered NIC activity) went from 2,721 edges to **4,434**. Most
of that was a correctness fix rather than new reach: a CIN embeds whichever NIC edition
was current at incorporation, so 600 of the 1,609 MCA rows carried NIC-2004 codes that the
lookup table had never heard of and silently dropped. It also stopped ranking companies
that have been struck off.

`LIKELY_MAKES` is new: **434 edges over 333 companies**, derived from what a firm says on
its own site, mapped onto the Fourth Schedule. It is better evidenced than the NIC path —
tier 2 input, and the quoted sentence travels with the edge so a reader can judge it — and
those 333 companies are entirely additive, because website-sourced firms and
registry-sourced firms barely overlap. Six capabilities feed it; `powder` and `aerosol`
are deliberately excluded as too ambiguous to map to a category.

Neither edge is a substitute for `PERMITS`. They answer "who could plausibly make this",
which is a buyer's question. "Who may lawfully make this" still has no answer, and the
zero above is that answer.

It was also specced wrong: S1 (CDSCO approved sites) was named as the cosmetics facility
spine. Of its 911 rows, **3** are cosmetics-regime. See `catalog/decisions/0001`.

## Why so many companies stop at trust L1

```sql trust
select trust_level, companies from catalog.trust order by trust_level
```

<BarChart data={trust} x=trust_level y=companies title="Companies by trust level" yFmt='#,##0'/>

Most companies are Udyam-registered and nothing more: registry membership proves they
exist (L1) and says nothing about permission or activity. Until 2026-10-18 the national
Udyam rows never carried their registration flag and sat at L0; the bar moved when that
was fixed, not because anything new was learned about the firms.

**S16 — GSTIN verification** is blocked on a paid provider key. It is what would lift a
company from L2 to L3: the difference between a number that passes a checksum and a number
that is currently active and belongs to this firm. `gst_record` holds 0 rows. We hold a
GSTIN at all for only 54 companies, so finding GSTINs is the binding problem before
verifying them is (see `TRUST_PLAN.md`).

Two rules constrain what may ever be done with it:

- **Never publish filing history.** Verify against it, rank with it, warn a buyer
  privately. "Hasn't filed in 8 months" on a public profile is a defamation problem.
- **DPDP.** A proprietorship GSTIN derives from an individual's PAN. Where
  `is_proprietorship`, it gets person-table handling — consent basis recorded, deletable —
  not industrial-spine handling.

## Derived edges are not evidence

```sql edges
select rel, n, derived from catalog.edges order by n desc
```

<BarChart data={edges} x=rel y=n series=derived swapXY=true title="Edges, derived vs asserted" yFmt='#,##0'/>

The largest edge counts are derived (tier 4). They rank and suggest; they never answer a
compliance question, and the `tier4_never_answers_compliance` golden query enforces that
mechanically rather than by convention.

`IN_CLUSTER_WITH` is absent by choice — it is a clique, and `Cluster(Ahmedabad-Vapi)` alone
has ~17,000 neighbours. A two-hop traversal answers it. See `catalog/decisions/0013`.

## The other empty edge

`THIRD_PARTY_MANUFACTURED_BY` holds **0 edges**. The ontology declares it and
`catalog/decisions/0002` routes every "who manufactures for whom" question to it, because
the CDSCO register gives a pairing whose direction is unknown. Nothing has ever fed it.

SEBI filings were the obvious candidate — free, full-text, legally attested, and this
project already has the chunking and entity-linking machinery. Two were read end to end,
and they say something specific about why this is hard.

Honasa (Mamaearth) outsources all manufacturing. Its DRHP discloses that it sourced from
**37 contract manufacturers** and that the top three were up to 82% of purchases. It names
**none of them** — the document contains zero `M/s`. Akums, a manufacturer, names its
customers freely: Ipca, Alembic, Alkem, Blue Cross, Mylan.

That asymmetry is regulatory, not editorial. A filer must disclose **customer**
concentration, because losing a customer is a material risk to it. Supplier identity is
not required and is commercially sensitive. So the relationship is only ever visible from
the factory's filing, pointing outward — and Indian cosmetics contract manufacturing is
proprietorships and partnerships, which never file with SEBI at all.

The edge stays empty, and the reason is now written down rather than open. Same commitment
as `PERMITS`. See `catalog/decisions/0015`.

## Blocked on a key, not on a decision

**S3 — MCA company master** needs a data.gov.in key with MCA access. Worth noting that
unblocking it would not fix the spine: MCA cannot see proprietorships and partnerships,
which are most of this industry by headcount. Udyam registers by enterprise rather than
legal form, which is why it became the spine instead.
