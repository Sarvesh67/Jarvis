---
title: Sources
description: Every source the graph is built from, what it contributes, and how far to trust it.
---

Each row is a card in `catalog/sources/`. The card carries the part this table cannot: what
the source is for, the defects found by building it, and the `do_not` block listing what it
may never be used to claim.

```sql by_tier
select
  tier,
  count(*)                                   as sources,
  sum(raw_rows)                              as rows,
  sum(case when status='live' then 1 else 0 end) as live
from catalog.sources
group by tier order by tier
```

<DataTable data={by_tier}>
  <Column id=tier title="Tier"/>
  <Column id=sources/>
  <Column id=live/>
  <Column id=rows fmt='#,##0'/>
</DataTable>

Tier 1 is the regulator and is the only tier that may answer a licence or compliance
question. Tier 2 is company-declared, tier 3 observed, tier 4 inferred.

## Every source

```sql sources
select
  id, name, tier, status, jurisdiction,
  raw_rows, coverage_pct, defects, guardrails, last_seen
from catalog.sources
order by tier, raw_rows desc
```

<DataTable data={sources} rows=20 search=true>
  <Column id=id title="ID"/>
  <Column id=name title="Source" wrap=true/>
  <Column id=tier title="T" align=center/>
  <Column id=status contentType=colorscale colorScale={['#e5e7eb']}/>
  <Column id=jurisdiction title="Juris." align=center/>
  <Column id=raw_rows title="Rows" fmt='#,##0'/>
  <Column id=coverage_pct title="Cov %" fmt='0.0'/>
  <Column id=defects title="Defects" align=center/>
  <Column id=guardrails title="Do-nots" align=center/>
  <Column id=last_seen title="Landed"/>
</DataTable>

An empty **Cov %** means coverage is genuinely unmeasured, not that it is zero. Udyam is
the important case: the registry has no activity index, the cosmetics slice is cut
client-side, and there is no honest denominator. Saying so is the point — see
`catalog/decisions/0006`.

## Volume by source

```sql volume
select name, tier, raw_rows from catalog.sources where raw_rows > 0 order by raw_rows desc
```

<BarChart data={volume} x=name y=raw_rows series=tier swapXY=true title="Rows landed, by source" yFmt='#,##0'/>

One source contributes most of the rows. That is the MSME long tail, and it is the
reason the graph sees proprietorships and partnerships that MCA structurally cannot.

## Defects are a feature of the card, not a warning about it

Every source lies to you in some specific way. A card with defects recorded is one that
has been read carefully; a card with none is usually one nobody has stress-tested. The
most expensive ones found here:

- A keyword list containing the bare word `CHEMICAL` flagged a forensic science
  laboratory as a cosmetics testing lab, inflating the count from 184 to 442.
- Treating Raigarh (Chhattisgarh) as a spelling variant of Raigad (Maharashtra) put nine
  firms in the wrong industrial cluster.
- The CDSCO register names both parties to a loan licence but not which one owns the
  plant — and different states fill the columns in opposite orders.

All of them are in `catalog/decisions/`.
