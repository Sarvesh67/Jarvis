# 0006 — Udyam is the spine MCA cannot be, and it partitions by pincode

**Status** accepted · **Affects** s17b_udyam, s03_mca

## Context
MCA was specced as the company spine. It is also blocked on a data.gov.in key.

## What the data actually says
MCA **structurally cannot see proprietorships and partnerships** — which are most of this
industry by headcount. Udyam registers by *enterprise*, not legal form, so it sees them.
Unblocking MCA would not fix the spine; it was the wrong spine.

Udyam's own constraint: the data.gov.in API caps every distinct query at 10,000 rows
against a 43.9M-record registry. District buckets can never be read completely — Surat
alone is 560,180 and Thane 684,886. Pincode is the only field that splits it into buckets
that mostly fit (Baddi is 6,280), and pincodes are globally unique, so no district filter
is needed alongside.

There is no server-side activity filter: `filters[Activities]` matches the whole JSON blob
and `q=` is ignored.

## Decision
Partition by pincode. Cut the NIC cosmetics slice client-side.

## Consequence
~5% yield — the price of the registry having no activity index. Coverage of the cosmetics
slice is genuinely **unmeasured** and must be reported as such, never estimated.

## Amended 2026-10-29 — measured yield, and the sweep must land what it reads
The first national sweep made 12,271 requests over 3,264 of the 4,842 cluster pincodes,
scanned about 7.3M records and kept 119,120. That is a **1.6% yield**, not ~5%. 165
pincodes hit the 10k window cap.

It also landed no raw page. Every non-matching row was discarded at read time, so widening
the NIC slice ([0017](0017-scope-is-the-cosmetics-bill-of-materials.md),
[0027](0027-packaging-codes-follow-the-registrys-own-text.md)) costs a full re-sweep. The
next sweep (SOURCES_PLAN Phase 1) lands every page through `lib.land()` with a
`fetch_manifest` row, and applies the NIC filter downstream, so that the sweep after it
costs nothing. It also covers the 1,578 pincodes never swept. The over-cap pincodes stay a
known hole: the API has no activity filter that could split them.
