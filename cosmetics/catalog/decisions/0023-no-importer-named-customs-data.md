# 0023 — No importer-named Indian customs data

**Status** accepted 2026-10-29 · **Affects** `TradeFlow`, `IMPORTED`, price benchmarks

## Context
Bill-of-entry data would say who imports which ingredient, from whom, at what price. That
is exactly what a buyer would like to know. Volza, Seair, Eximpedia, Zauba and Panjiva
sell it.

## What the data actually says
Customs Act s.135AA (Finance Act 2022) makes it an offence to publish customs value,
classification or quantity data that identifies the importer or exporter. The exceptions
are publication by or for the government, and use of those publications for trade-trend
analysis. Press reports describe a 2025 probe into customs data leaked to private sites.

## Decision
1. Indian importer-named or exporter-named customs data is not bought, landed or shown.
2. Aggregates are used instead: tradestat EIDB (HS-8 × country × month) and DGCI&S. They
   name nobody.
3. US import manifests that name Indian shippers are allowed at tier 3, without prices,
   after the vendor's terms are checked.

## Consequence
`IMPORTED` / `TradeFlow` stay empty for Indian importers. That is an honest empty edge, in
the sense of [0012](0012-permits-is-legitimately-empty.md).
