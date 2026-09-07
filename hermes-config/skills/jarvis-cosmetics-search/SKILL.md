---
name: jarvis-cosmetics-search
description: "Answer questions about the India cosmetics supply chain — contract manufacturers, packaging converters, ingredient suppliers, accredited testing labs, and what the Cosmetics Rules 2020 permit — from the cosmetics knowledge graph. Bounded, whitelisted queries you then reason over."
version: 1.0.0
platforms: [macos]
metadata:
  hermes:
    tags: [cosmetics, skincare, supply-chain, regulatory, knowledge-graph, india, cdsco, nabl]
    related_skills: [jarvis-knowledge]
---

# Cosmetics Supply-Chain Search

Answer buyer, supplier and compliance questions for the **India cosmetics / skincare** domain
(face and skin care wedge; clusters Baddi, Haridwar-Selaqui, Ahmedabad-Vapi, Palghar-Tarapur).

A deterministic tool runs one of a **fixed set of reviewed queries** and returns bounded JSON.
**You reason over that JSON.** You never write database queries and never invent a company,
licence number, GSTIN or certificate.

> This is a DIFFERENT domain from `jarvis-vendor-search`, which is the Surat **textile** graph.
> Do not use the textile tool for cosmetics questions, and do not use this one for textiles.

## The tool

```bash
COS=/Users/sarvesh/Documents/Jarvis/cosmetics
$COS/.venv/bin/python -m pipeline.router --template <ID> --param k=v --json
```

Always pass `--json`. Always pass `--template` (you choose it — do not make the tool guess).
Run `--list` to see every template with its parameters.

## Choosing the template

| The user is asking | template | params |
|---|---|---|
| who is in this business in a place | `cosmetics_firms_in_cluster` | `cluster` |
| who can make my product for me (third party / private label / contract) | `third_party_manufacturers` | `cluster` |
| who is **licensed** (COS-8/COS-9) to make a category | `manufacturers_for_category_in_cluster` | `category`, `cluster` |
| who holds a loan licence | `loan_licence_holders` | — |
| who supplies an ingredient | `suppliers_of_ingredient` | `ingredient` |
| who makes packaging (bottles, jars, tubes, cartons) | `packaging_converters_in_cluster` | `component`, `cluster` |
| where can I get it tested | `testing_labs_for_cosmetics` | `state`, `cluster` |
| is X allowed / restricted in India | `ingredient_restriction_india` | `ingredient` |
| is X restricted in the EU | `ingredient_restriction_eu` | `ingredient` |
| what standard applies to a category | `standard_for_category` | `category` |
| what can replace ingredient X | `substitutes_for_ingredient` | `ingredient` |
| everything known about one company | `company_profile` | `company` |
| EU-restricted ingredients on Indian shelves | `eu_restricted_in_indian_products` | — |

Pass parameter values **as the user said them** — "Baddi", "face serum", "Gujarat", "niacinamide".
The tool resolves them against the graph's own vocabulary and reports how in `param_resolution`.
If a value comes back `unresolved`, say so and ask the user to rephrase; do not substitute a guess.

## Reading the result

Every response carries the honesty metadata — use it, don't strip it:

- **`tier_floor` / `tier_meaning`** — the WORST evidence the answer rests on.
  `1 regulator` · `2 company-declared` · `3 observed/scraped` · `4 inferred`.
  **Tier 4 never answers a licence, compliance or restriction question.** If a tier-4 result is
  the only thing you have for such a question, say the graph cannot answer it.
- **`note`** — a caveat about the template that you should pass on when it changes the meaning.
- **`empty_reason`** — present when `rows` is empty. Zero rows is a COVERAGE GAP, never proof
  that none exist. Relay the reason and the suggested alternative rather than saying "there are
  none".
- **`evidence_basis`** (firm searches) — "current Udyam activity" is what a firm declares now;
  "NIC recorded at incorporation" is what it registered as years ago and may be stale. Rank and
  caveat accordingly.
- **`evidence` / `claimed_on_their_site`** — the sentence the company actually wrote. Quote it
  when you assert a capability.
- **`has_licence_evidence`** — false means we have NO cosmetics licence on file for that firm.
  Presence in the registry is not permission to manufacture.

## Hard rules

- Use ONLY the companies, labs and rules the tool returns.
- **Never present registry presence as a licence.** A firm appearing in a cluster search is not
  licensed to make cosmetics; only `manufacturers_for_category_in_cluster` and
  `has_licence_evidence: true` speak to that. State licence coverage is currently very thin —
  when the user asks "who is licensed", say plainly how little is on file.
- **Never mix jurisdictions.** An EU annex restriction has no force in India. The tool labels
  EU results; keep that label in your answer.
- Do not report a contact person, email or phone for a testing lab — that is personal data held
  outside the graph on purpose.
- If the right template does not exist for the question, say so and answer from what the closest
  one returns, labelled as such. Do not fall back to writing your own queries.

## Worked example

User: *"I need someone in Baddi who can make a face serum for my brand."*

```bash
$COS/.venv/bin/python -m pipeline.router --template third_party_manufacturers --param cluster=Baddi --json
```

If that returns zero rows, read `empty_reason` — contract-manufacturing claims are read from
company websites and most cluster MSMEs have no website — then run
`cosmetics_firms_in_cluster` for `cluster=Baddi`, and tell the user these are firms the registry
shows making cosmetics there, that we have no licence on file for them, and that the next step is
verifying a COS-8.
