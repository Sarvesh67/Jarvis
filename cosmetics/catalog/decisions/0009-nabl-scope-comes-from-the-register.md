# 0009 — NABL accreditation scope comes from the register, not from keywords

**Status** accepted · **Affects** s11_nabl

## Context
Identifying which of 6,682 accredited labs can test cosmetics.

## What the data actually says
A keyword list containing the bare word `CHEMICAL` flagged a **CENTRAL FORENSIC SCIENCE
LABORATORY** as a cosmetics testing lab, and inflated the count from 184 to **442** —
a 140% overcount of the most operationally load-bearing number in the source.

## Decision
"Accredited for cosmetics" means the register's own scope fields — Cosmetics & Essential
Oils, AYUSH, or Soap-Detergent-Toiletries — and nothing else.

## Consequence
184 labs, not 442. Pharma accreditation is recorded as **adjacent capability**, never as
cosmetics accreditation.
