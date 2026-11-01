---
title: Cosmetics supply-chain graph
description: What the data holds, where it came from, and what it may be used to claim.
---

An ontology-guided knowledge graph of the Indian B2B cosmetics supply chain: who can
legally make what, where, from which ingredients, under which standard — and how much of
that can actually be proved.

This site is the reading surface. The figures come from `make catalog`, which measures the
live stores; the judgement comes from the source cards in `catalog/`, which are written by
hand and reviewed.

```sql totals
select
  (select sum(n) from catalog.nodes)                        as nodes,
  (select sum(n) from catalog.edges)                        as edges,
  (select count(*) from catalog.sources where status='live') as live_sources,
  (select sum(rows) from catalog.tables)                     as pg_rows
```

<BigValue data={totals} value=nodes title="Graph nodes" fmt='#,##0'/>
<BigValue data={totals} value=edges title="Graph edges" fmt='#,##0'/>
<BigValue data={totals} value=live_sources title="Live sources"/>
<BigValue data={totals} value=pg_rows title="Postgres rows" fmt='#,##0'/>

## What is in the graph

```sql nodes
select label, n from catalog.nodes order by n desc
```

<BarChart data={nodes} x=label y=n swapXY=true title="Nodes by label" yFmt='#,##0'/>

## Read this before trusting a number

**Tier decides what a fact may claim.** Tier 1 is the regulator and is the only tier that
may answer a licence or compliance question. Tier 2 is a company's own claim, tier 3 is
something observed on a web page, tier 4 is inferred. Those three rank and suggest — they
never permit.

**The evidence grade is not a quality score, and it never ranks.** Read `trust_grade` (flagged, G0–G4) and the facets `trust_licence` / `trust_activity` / `trust_track`; `trust_level` is deprecated. Filter licences with `trust_licence = 'verified'`.

**Pin `jurisdiction` on every rule.** The graph holds both Indian and EU regulation, and
an EU restriction is not an Indian restriction.

**An empty edge is a real answer.** See [what is missing](/gaps) — several zeros on this
site are deliberate.

## Where to go next

- [Sources](/sources) — the 16 cards, their coverage and their defects
- [Gaps](/gaps) — what is not here, and what that costs
