# 0002 — A loan licence names two firms but not which one owns the plant

**Status** accepted · **Affects** s01_cdsco, `LOAN_LICENCE_WITH`

## Context
The CDSCO register has `Premise Name` and `Loan Premises Name`. Reading them as
(factory, brand) would give the contract-manufacturing direction directly.

## What the data actually says
State authorities fill the two columns in opposite conventions.
`Pure&Cure → Dr. Reddy's` puts the factory first. `GSK → Encube` puts it second.
P&G Health appears on *both* sides in different rows.

## Decision
The pairing is tier 1; the direction is not. `LOAN_LICENCE_WITH` is written **both ways**
with `direction_known:false`.

## Consequence
Anything that needs to say who manufactures *for* whom must use
`THIRD_PARTY_MANUFACTURED_BY`, which requires evidence this source cannot supply.
