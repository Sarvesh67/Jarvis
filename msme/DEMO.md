# YC Demo — MSME Supply-Chain Second Brain (~2 min)

Screen recording + voiceover. Product-first: show the thing working before explaining how.

**Word-for-word script → [DEMO_SCRIPT.md](DEMO_SCRIPT.md)** (main cut, 60s cut, delivery notes,
YC follow-up answers). This file is the checklist, storyboard and verified numbers.

---

## Before you hit record

**Data / services**
- [ ] FalkorDB + LiteLLM up: `docker ps | grep -E "falkor|litellm"`
- [ ] Dashboard responds: `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/` → 200
- [ ] Cognee layer populated: `cognee/.venv/bin/python cognee/query.py msme "embroidery firms and the materials they use"` returns firm text (NOT "No relevant knowledge")
      *(Q3 reads `msme_semantic_graph`; Q1/Q2 and the dashboard graph read the 326k `msme_graph` backbone — two graphs by design, see README.)*
- [ ] Focus view renders: Knowledge engine → Graph → type a firm → Focus
- [ ] **Pre-warm the agent**: send one throwaway message in the msme Chat tab (first message spawns hermes and is slow — never record this)

**Screen hygiene**
- [ ] 1080p+, browser at ~110% zoom, full-screen, no other tabs/bookmarks bar
- [ ] Hide anything personal; the bearer token is embedded in the page — don't open devtools on camera
- [ ] Close Slack/Mail; silence notifications
- [ ] Have the three questions in a scratch file to paste (typing live wastes seconds)

**Language rules (credibility)**
- Say **"registered"**, never "verified" — GST verification is cut (no GSTIN in Udyam data).
- Every number you say must match what's on screen (see Numbers below).
- Jump-cutting agent "thinking" time is fine. **Never** fake an answer — show real output.

---

## Storyboard

### 0:00–0:10 — Problem (screen: graph focus view, slow zoom)
> "Surat has over three hundred thousand registered textile businesses. Almost none of them
> can tell you who to buy from, or who might buy from them. That still runs on word of mouth."

### 0:10–0:25 — What it is (screen: dashboard → MSME Second Brain project)
> "This is a supply-chain second brain. It's built automatically from India's public Udyam
> business registry — no data entry, no onboarding."

Show the header: node/edge counts visible on the Knowledge tab.

### 0:25–1:05 — Core demo — the chatbox (THE money shot)
Go to **Chat**. Paste Q1:

> **"Find me suppliers of embroidered dress material in Surat."**

While it runs:
> "It's not searching text. Every firm's registered activity is mapped to what it *produces*
> and what it *consumes* — so one firm's output is another firm's raw material. That's the join."

When the answer lands, hover the evidence (the shared material per match).

Then paste Q2 — flip the direction:

> **"I make lace. Who would buy from me?"**

> "Same graph, asked backwards — now it's demand. This is the part a directory can't do."

### 1:05–1:25 — The graph (screen: Knowledge engine → Graph → Focus a real firm)
Type a real embroidery firm name → **Focus**.

> "Here's one real unit. Green is what it makes and what it needs. Orange is who it can trade
> with — each link is a specific shared material, not a guess."

Let the concentric layout settle before you talk over it.

### 1:25–1:45 — Scale & economics (screen: stay on graph, or Overview for budget)
> "Half a million registrations processed into three hundred twenty-six thousand connected
> firms across thirteen hundred industry codes. Classifying every industry code cost about
> seven cents, once. A search returns in a fifth of a second."

### 1:45–2:00 — Vision (screen: zoom out on the graph)
> "India has sixty-three million MSMEs. They're the supply chain — and they're invisible to
> each other. We're building the layer that connects them."

---

## Numbers (verified — say these exactly)

| Claim | Actual | Where to see it |
|---|---|---|
| Udyam rows processed | 539,453 | pipeline log |
| Connected firms in graph | 326,816 | `:Enterprise` count / Knowledge tab |
| Total graph nodes | 328,371 | Knowledge tab header |
| Total graph edges | 722,418 | Knowledge tab header |
| Distinct NIC industry codes | 1,301 parsed / 1,104 in graph | `data/nic2008.csv` |
| **Industry-code enrichment (one-time)** | **~$0.075** | LiteLLM /ui, msme key |
| **Total AI spend to date** (incl. semantic layer) | **~$0.27** | dashboard budget bar |
| Graph search latency | ~0.2 s | `msme.search --graph` timing |
| Firms per NIC code enriched | 1,171 distinct codes | pipeline log |
| Semantic layer (Q3) | 465 chunks · 835 entities · 4,676 triplets | `msme_semantic_graph` |

> The "~7 cents" figure covers **classifying all 1,301 industry codes** — the step that makes the
> whole 326k-firm graph possible. If you'd rather quote one number for everything, say
> **"under thirty cents"** (total project spend, visible on the budget bar on camera).

**Do not claim:** GST verification, revenue/employee data, live registry sync, or anything about
firms outside Surat — the dataset is Surat-only.

---

## The three questions (pre-tested)

1. `Find me suppliers of embroidered dress material in Surat.`
2. `I make lace. Who would buy from me?`
3. `What do you know about the embroidery cluster in Surat?`  ← semantic / graphRAG

If the agent picks the wrong tool on Q3, ask it more concretely
("use the knowledge graph to tell me about…").

---

## Fallbacks if something breaks on the day

- **Agent hangs** → cut, re-ask; the CLI equivalent still proves it:
  `msme/.venv/bin/python -m msme.search --graph --need "dress material" --role supplier --district surat --top 5`
- **Focus view empty** → the firm name must exist; use one confirmed present (e.g. `1910 Enterprise`).
- **Cognee returns nothing** → skip Q3, keep Q1/Q2 (they use the deterministic graph, always available).
