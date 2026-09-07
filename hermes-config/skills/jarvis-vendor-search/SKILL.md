---
name: jarvis-vendor-search
description: "Find suppliers, buyers, or competitors for an MSME from the Surat textile supply graph — a bounded, ranked, filtered vendor shortlist you then reason over."
version: 1.0.0
platforms: [macos]
metadata:
  hermes:
    tags: [msme, supply-chain, vendors, knowledge-graph, surat, textile]
    related_skills: [jarvis-knowledge]
---

# MSME Vendor Search

Answer "who can supply me X", "who would buy my X", or "who competes with me" for a Surat textile
MSME. A deterministic tool returns a **bounded, ranked shortlist** (material match + geography + GST
trust); **you reason over that shortlist** — you never invent firms or enumerate the whole registry.

## When to use
- The user asks to find suppliers / buyers / customers / competitors, source a raw material, or
  place a finished product. (For general Q&A over the graph, use **jarvis-knowledge**.)

## How it works
Supply links are inferred through **materials**: a firm that *produces* material M can sell to any
firm that *consumes* M. The tool does the filtering + top-K; you interpret and advise.

## Two query shapes

**1. By material + role** — "find cotton-yarn suppliers in Surat, GST-verified":
```bash
/Users/sarvesh/Documents/Jarvis/msme/.venv/bin/python -m msme.search --graph \
  --need "cotton yarn" --role supplier --district surat --gst-verified --top 20
```
- `--role supplier` = who can sell me this; `--role buyer` = who needs this (find customers).
- Filters: `--state`, `--district`, `--pincode`, `--gst-verified`. `--top N` caps the list.
- Drop `--graph` to use the DuckDB pre-filter over the full registry instead of the graph slice.

**2. By firm** — "who are the buyers / suppliers / competitors of THIS firm":
```bash
/Users/sarvesh/Documents/Jarvis/msme/.venv/bin/python -m msme.search \
  --of "395010-vaghasiya_embroidery" --rel buyers --top 20
```
- `--rel` is `suppliers` | `buyers` | `competitors`. Each result lists `via` — the shared
  materials that justify the link (your evidence).

## Steps
1. Map the user's request to a `need` (material/product) or a firm `udyam` id, a `role`/`rel`, and
   any geography / GST filter they stated.
2. Run the tool. Re-run with a widened filter (drop district, then state) only if the list is thin.
3. **Reason over the JSON**: recommend the best-fit vendors, explain *why* using the `via` materials
   and proximity/GST, and flag when the shortlist is small or all-unverified.

## Rules
- Use ONLY firms the tool returns — never invent a vendor or a GSTIN.
- The list is already bounded and ranked; don't ask for "all" vendors — surface the top matches and
  say how many matched vs were returned (the tool logs it).
- If nothing matches, say so plainly and suggest the nearest widening (drop a filter, related
  material) rather than guessing.
