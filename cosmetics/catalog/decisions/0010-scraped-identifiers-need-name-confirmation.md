# 0010 — Scraped identifiers are promoted only on name confirmation

**Status** accepted · **Affects** s14_sites, s09_chemexcil, pipeline/reconcile.py

## Context
A GSTIN found on a company's website is the cheapest route from trust L0 to a real
identifier.

## What the data actually says
Directories legitimately list a **parent or group site** against a subsidiary. CHEMEXCIL
lists Bombay Perfumery against `aacipl.com` — verified against the raw HTML as their data,
not a parse error. A GSTIN lifted from that page would merge two unrelated entities.

## Decision
An identifier scraped from a site is promoted to a spine field **only when the company's
own name appears on that site**. Every candidate is checksum-validated first.
Unconfirmed identifiers stay in the payload, visible but not load-bearing.

## Consequence
Fewer promoted GSTINs, no false merges. The cost is borne in trust level, which is the
right place for it to show.
