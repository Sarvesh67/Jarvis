---
name: jarvis-cosmetics-search
description: "Answer questions about the India cosmetics supply chain — contract manufacturers, packaging converters, ingredient suppliers, accredited testing labs, and what the Cosmetics Rules 2020 permit — from the cosmetics knowledge graph. Reviewed templates for regulatory facts, graph context for open questions, and validated Cypher you write yourself for counting and ranking."
version: 2.0.0
platforms: [macos]
metadata:
  hermes:
    tags: [cosmetics, skincare, supply-chain, regulatory, knowledge-graph, india, cdsco, nabl, graphrag, cypher]
    related_skills: [jarvis-knowledge]
---

# Cosmetics Supply-Chain Graph

The India cosmetics / skincare domain: manufacturers, packaging converters, ingredient
suppliers, NABL testing labs, and the Cosmetics Rules 2020. Clusters are Baddi,
Haridwar-Selaqui, Ahmedabad-Vapi and Palghar-Tarapur.

> A DIFFERENT domain from `jarvis-vendor-search`, which is the Surat **textile** graph.
> Never answer a cosmetics question with the textile tool.

```bash
COS=/Users/sarvesh/Documents/Jarvis/cosmetics
$COS/.venv/bin/python -m pipeline.router <args>
```

## You have three paths. Choose by what kind of claim you are about to make.

| The question is | Path | Why |
|---|---|---|
| licence, restriction, standard, "is X allowed", "who is licensed" | **template** | These are the only reviewed queries. A regulatory claim may come from nowhere else. |
| open, several entities at once, "tell me about", "help me think" | **context** | Returns the actual neighbourhood — entities, edges, provenance, rule text — to reason over. |
| counting, ranking, "how many", "top N", any join no template covers | **cypher** | You write it. Only this path can aggregate or be exhaustive. |

Chain them freely: context to orient, cypher to compute, template to confirm a regulatory
point. You already chain templates; keep doing that.

---

## 1. Templates — for regulatory facts

```bash
$COS/.venv/bin/python -m pipeline.router --template <ID> --param k=v --json
$COS/.venv/bin/python -m pipeline.router --list           # every template + its params
```

| Asking | template | params |
|---|---|---|
| who is in this business here | `cosmetics_firms_in_cluster` | `cluster`, `district` |
| who can make it for me (third party / private label) | `third_party_manufacturers` | `cluster`, `district` |
| who is **licensed** for a category | `manufacturers_for_category_in_cluster` | `category`, `cluster`, `district` |
| loan licence holders | `loan_licence_holders` | — |
| who supplies an ingredient | `suppliers_of_ingredient` | `ingredient` |
| packaging (bottles, jars, tubes, cartons) | `packaging_converters_in_cluster` | `component`, `cluster`, `district` |
| where can I get it tested | `testing_labs_for_cosmetics` | `state`, `cluster`, `district` |
| is X allowed / restricted in India | `ingredient_restriction_india` | `ingredient` |
| is X restricted in the EU | `ingredient_restriction_eu` | `ingredient` |
| standard for a category | `standard_for_category` | `category` |
| what can replace ingredient X | `substitutes_for_ingredient` | `ingredient` |
| everything about one company | `company_profile` | `company` |
| EU-restricted ingredients on Indian shelves | `eu_restricted_in_indian_products` | — |

**If the user names a DISTRICT, pass `district`, not `cluster`.** A cluster spans several
districts — answering "Navsari" with the whole Ahmedabad-Vapi belt is a wrong answer dressed
up as a generous one. Use `cluster` only when they name a cluster or a broad region.

Pass values **as the user said them** — "Navsari", "Baddi", "face serum", "niacinamide".
The tool grounds them against the graph's vocabulary and reports how in `param_resolution`.
If one comes back `unresolved`, say so; never substitute a guess.

`--limit N` raises the row cap (default 50), `--rows 0` prints them all, `--json` always
returns every row. A short result is not evidence the tool is lossy.

## 2. Context — for open questions

```bash
$COS/.venv/bin/python -m pipeline.router --mode context "tell me about the Baddi cluster"
# then steer a further hop from anything interesting it returned:
$COS/.venv/bin/python -m pipeline.router --expand Company:c_ab12cd34 Cluster:HP_BBN
$COS/.venv/bin/python -m pipeline.router --expand Company:c_ab12cd34 --edge-types OPERATES LICENSED_UNDER
```

Returns `nodes`, `edges`, `chunks` (cited rule/company text), and `seeds` showing what it
matched on. Every node carries a `ref` — pass those refs back to `--expand`.

**It is a SAMPLE, not a census.** Never count or rank from it; use cypher for that. The
`trust` field says this explicitly — respect it.

## 3. Cypher — when you need to compute

```bash
$COS/.venv/bin/python -m pipeline.router --schema      # ALWAYS read this first
$COS/.venv/bin/python -m pipeline.router --cypher "MATCH (c:Company)-[:SUPPLIES_PACKAGING]->(p:PackagingComponent)
  RETURN p.type AS component, count(DISTINCT c) AS firms ORDER BY firms DESC"
```

**Read `--schema` before writing a query.** It is generated from the live graph and lists
every label, every relationship type with its row count, key properties, the controlled
vocabularies, the hub nodes, and the mistakes that have already caused wrong answers here.
Never guess a label or property name.

The query is validated before it runs. It must be read-only and bounded; it is rejected if
it touches `RegulatoryRule` without pinning `jurisdiction`, or leans on derived tier-4
edges for a compliance question. **A rejection tells you why and names the offending
fragment — repair the query and retry.** Do not give up and answer from memory.

The executed query comes back with the results. Quote it when the answer matters.

---

## Reading any result

- **`answered_by`** — `template` | `graph_context` | `generated_cypher`. Say which you used
  when the distinction affects how much weight the answer carries.
- **`tier_floor` / `_tier` on edges** — the worst evidence involved.
  `1 regulator` · `2 company-declared` · `3 observed/scraped` · `4 inferred`.
  **Tier 4 never answers a licence, compliance or restriction question.**
- **`note` / `trust`** — caveats that change the meaning. Pass them on.
- **`empty_reason`** — zero rows is a COVERAGE GAP, never proof none exist. Relay the
  reason and the suggested alternative.
- **`evidence_basis`** — "current Udyam activity" is what a firm declares now; "NIC recorded
  at incorporation" is what it registered as years ago and is often stale.
- **`evidence` / `claimed_on_their_site`** — the sentence the company actually wrote. Quote
  it when you assert a capability.
- **`has_licence_evidence: false`** — we hold NO cosmetics licence for that firm.

## Hard rules

- **Regulatory claims come from templates only.** Context and Cypher answers are
  exploratory — label them that way. Never state "X is licensed" or "Y is legal in India"
  on the strength of a query you wrote.
- **Registry presence is not permission to manufacture.** State licence coverage is thin;
  when asked who is licensed, say plainly how little is on file.
- **Never mix jurisdictions.** An EU annex restriction has no force in India. Keep the label.
- Use only the companies, labs and rules the tools return. Never invent a firm, licence
  number, GSTIN or certificate.
- Do not report contact people, emails or phones — that data is held outside the graph on
  purpose (DPDP).

## Worked example

*"I need someone in Baddi who can make a face serum, and I want to know how that cluster
compares to Ahmedabad."*

1. `--template third_party_manufacturers --param cluster=Baddi --json`
2. Empty? Read `empty_reason` — claims come from websites and most cluster MSMEs have none.
   Fall back to `--template cosmetics_firms_in_cluster --param cluster=Baddi`.
3. For the comparison, no template covers it, so compute it:
   `--cypher "MATCH (c:Company)-[:LOCATED_IN]->(cl:Cluster) WHERE 'cosmetics_core' IN c.roles RETURN cl.name AS cluster, count(c) AS firms ORDER BY firms DESC"`
4. Answer: name the firms, say they are registry-listed with no licence on file, give the
   cluster comparison, and mark the comparison as exploratory.
