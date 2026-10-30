# 0022 — No marketplace scraping

**Status** accepted 2026-10-29 · **Affects** s10_indiamart, any marketplace or retailer source

## Context
The build spec listed IndiaMART as a proof of concept. Marketplaces are where MOQ, pack
sizes and indicative prices appear, and those are layer-D data that no registry carries.

## What the data actually says
Checked 2026-10-25/26:

| Site | What it says |
|---|---|
| **IndiaMART** | Its terms ban systematic retrieval "through robots, spiders…" without written permission. robots.txt blocks ClaudeBot. Its only API is the seller-side Lead Manager, for a paid seller's own leads. |
| **TradeIndia** | robots.txt blocks search, supplier and paged listing pages |
| **Amazon.in** | Terms ban data mining |
| **Nykaa** | Returned 403 even for robots.txt |
| **UL Prospector, SpecialChem, Knowde** | Terms ban it, a Cloudflare 403, and robots.txt blocks ClaudeBot respectively |

## Decision
None of these is crawled. s10 stays `not_built`, and its card's `do_not` cites the terms
and robots.txt. The "PoC" in the build spec is withdrawn.

## Consequence
The same data arrives lawfully, and for exactly the suppliers that matter:

- supplier-claimed profiles;
- responses on our own RFQ rail ([0025](0025-the-rfq-rail-is-a-source.md)).

A licensed directory (Chemical Weekly Buyers' Guide) is the paid alternative, and it is
an accelerator, not a dependency.
