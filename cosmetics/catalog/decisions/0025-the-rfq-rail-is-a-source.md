# 0025 — The RFQ rail is a source, with a card like any other

**Status** accepted 2026-10-29 · **Affects** PRD Priority 3, Postgres procurement tables, trust evidence

## Context
The PRD's loop is: shortlist, contact, RFQ, response, quote, decision, then fulfilment
and quality. It asks: "What is the first closed-loop procurement event we can reliably
track?"

## What the data actually says
More than 30 public candidates were probed for price, MOQ, lead time and outcomes. None
carries them lawfully: marketplaces forbid scraping
([0022](0022-no-marketplace-scraping.md)), and customs data is barred
([0023](0023-no-importer-named-customs-data.md)). These fields exist only between a buyer
and a supplier.

## Decision
1. The rail is source `s31`, with a card and `do_not` entries.
2. Its tables live in Postgres only:
   - `buyer_request`
   - `shortlist`
   - `rfq` (one per BOM line × supplier)
   - `rfq_response` (quote, MOQ, lead time, sample terms, freight)
   - `outcome`
   - `interaction_event`
3. A quote is tier 2, supplier-declared. Response time and response rate are measured by
   us: `provenance_class: first_party`.
4. Outcomes climb TRUST_PLAN's ladder: buyer-confirmed → e-invoice QR verified →
   consented GSTR-2B.
5. Visibility:
   - A buyer sees their own quotes.
   - Aggregates appear only with ≥3 suppliers and ≥2 buyers per material.
   - Response metrics appear once there are ≥5 RFQs.
   - A "transacted with" signal appears only with ≥2 distinct buyers.
   - No negative signal is ever public (TRUST_PLAN §7.4).
6. No outcome becomes a graph edge until a later decision says how. The label `Event`
   belongs to trade fairs and is not used here.

**Answer to the PRD:** the first reliably trackable event is **an RFQ response received
through our rail**. We observe it fully, it needs no consent beyond that of the two
parties, and it yields layer-D data the day it happens.
