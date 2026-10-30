# 0021 — Who makes for whom comes from labels and state licence lists, not filings

**Status** accepted 2026-10-29 · **Affects** `THIRD_PARTY_MANUFACTURED_BY`, s02_state_fda, s24 (brand sites), `LOAN_LICENCE_WITH`

## Context
`THIRD_PARTY_MANUFACTURED_BY` holds 0 edges.
[0002](0002-loan-licence-direction-is-unknown.md) found that the CDSCO register names both
parties to a loan licence but not which one owns the plant.
[0015](0015-manufacturing-relationships-are-disclosed-from-the-factory-side.md) found that
brands keep manufacturer names out of their SEBI filings.

## What the data actually says
Two documents carry the direction, and both are published:

- **State licence lists.** Goa's drug regulator publishes its cosmetics loan-licence list
  as "Brand C/o Manufacturer". Example: *Opella Healthcare C/o Encube Ethicals, Madkaim,
  Ponda* (PDF dated 13 Oct 2026). This is tier 1, and it has a direction.
- **Product labels.** A cosmetic label must name its manufacturer, and D2C brands
  republish the label on their sites. Foxtale's product page names *Anaika Industry, Plot
  52, Mandvi Village, Virar East*. This is tier 2, and it has a direction. Disclosure
  varies: The Derma Co names only its marketer.

## Decision
1. Both write `THIRD_PARTY_MANUFACTURED_BY` (brand → manufacturer) with
   `direction_known: true` and the quoted line.
2. "Marketed by" is never read as "manufactured by".
3. CDSCO's `LOAN_LICENCE_WITH` stays undirected, per 0002, unless one of these confirms
   the pair.
4. 0015 is restated, not reversed: filings don't disclose the manufacturer, labels do.

## Consequence
Goa is small (3 loan and 6 own-manufacturing licences). The volume is in Maharashtra,
Gujarat, Himachal and Uttarakhand, which publish no list. RTI requests for those lists
run in parallel from Phase 0.
