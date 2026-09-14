# Demo Script — word-for-word

Read-aloud script for the screen recording. `[SCREEN]` = what's showing, **>** = what you say.
Checklist, fallbacks and verified numbers live in [DEMO.md](DEMO.md).

Delivery: talk like you're explaining it to one smart person, not presenting to a room.
Short sentences. Pause at the line breaks. Don't rush the demo answers — silence while a real
answer loads is *credibility*, not dead air.

---

## MAIN CUT (~2:10)

### [0:00–0:12] Cold open — the problem
`[SCREEN: graph focus view, already settled, slow zoom out]`

> Surat is India's biggest man-made textile hub.
> Three hundred and twenty-six thousand small businesses, in one city.
>
> Almost none of them can tell you who to buy from, or who could buy from them.
> That still runs on word of mouth.

### [0:12–0:28] What it is
`[SCREEN: dashboard → MSME project → Knowledge engine tab, counts visible]`

> This is a supply-chain second brain for those businesses.
>
> It builds itself out of India's public business registry.
> No data entry, no onboarding — nobody had to sign up for this to exist.

### [0:28–1:05] Demo 1 — supply *(the money shot)*
`[SCREEN: Chat tab. Paste Q1, hit send]`

> Let me ask it what an actual business would ask.

**Paste:** `Find me suppliers of embroidered dress material in Surat.`

*While it runs — talk over it:*

> It isn't searching text.
> Every firm's registered activity is mapped to what it **produces** and what it **consumes**.
> So one firm's output becomes another firm's raw material. That's the join.

`[SCREEN: answer lands — scroll so the list and the reasoning are both visible]`

> Real registered firms. And each one comes back with the specific material that connects them.
> Not a keyword match — an actual supply relationship.

### [1:05–1:25] Demo 2 — flip the direction
**Paste:** `I make lace. Who would buy from me?`

> Same graph, asked backwards. Now it's demand.
>
> This is the part a business directory can't do.
> A directory tells you who exists. It can't tell you who needs what you make.

### [1:25–1:45] The graph
`[SCREEN: Knowledge engine → Graph → type a real firm → Focus. Let the layout settle BEFORE speaking]`

> **Use one of these — verified present, with the densest neighborhoods (best visual):**
> `RANGRUT TEXFAB PRIVATE LIMITED` (29 materials) · `LAXMINARAYAN FASHION` (26) ·
> `MEENAKSHI TRENDZ` (26). Type it exactly.

> Here's one real unit, sitting in the middle of its own supply network.
>
> Green is what it makes and what it needs.
> Orange is who it can trade with — and every one of those links is a specific shared material.

### [1:45–2:00] Scale and cost
`[SCREEN: stay on the graph, or cut to Overview for the budget bar]`

> Half a million registry records, turned into three hundred twenty-six thousand connected
> businesses, across eleven hundred industry codes.
>
> Teaching it what every industry code makes and consumes cost about seven cents. Once.
> A search comes back in a fifth of a second.

### [2:00–2:10] Close
`[SCREEN: zoom out on the full graph]`

> India has more than sixty million registered small businesses.
> They *are* the supply chain — and they're invisible to each other.
>
> We're building the layer that connects them.
> We started in Surat, because if it works in the densest textile cluster in the country,
> it works anywhere.

---

## SHORT CUT (~60s) — if you need a tighter version

> Surat has three hundred twenty-six thousand small textile businesses.
> Almost none of them can tell you who to buy from, or who'd buy from them.
>
> This is a supply-chain second brain, built automatically from India's public business registry.
> No onboarding — nobody had to sign up for it to exist.

**Paste Q1** — `Find me suppliers of embroidered dress material in Surat.`

> It's not searching text. Every firm's registered activity is mapped to what it produces and what
> it consumes — so one firm's output is another's raw material. Each match comes back with the
> exact material that links them.

**Paste Q2** — `I make lace. Who would buy from me?`

> Same graph, asked backwards. That's demand — the thing a directory can't give you.

`[SCREEN: focus graph]`

> Half a million records into three hundred twenty-six thousand connected firms.
> Seven cents to classify every industry code. A fifth of a second per search.
>
> India has sixty million of these businesses. They're invisible to each other.
> We connect them.

---

## Delivery notes

- **Don't narrate the UI.** Never say "now I'm clicking on…" — say what it *means*.
- **Let real latency show.** A 3-second wait for a real answer is fine. Jump-cut only long agent
  thinking (>8s).
- **Numbers: say them once, confidently.** Every figure below is verified — but if you misspeak,
  re-record the line rather than correcting yourself on tape.
- **Never say "verified"** about GST — say **"registered"**. The Udyam data has no GSTIN.
- **Don't oversell.** No "AI-powered", no "revolutionary". The graph is the argument.
- If a question returns fewer results than you hoped, say so plainly and move on — a founder who
  shows real output beats one who edits around it.

---

## Claims to sanity-check before you record

These are yours to stand behind — verify you're comfortable defending each:

| Line in script | Status |
|---|---|
| "India's biggest man-made textile hub" | Widely cited for Surat — confirm your preferred phrasing |
| "more than sixty million registered small businesses" | Udyam registrations; check the current figure |
| 326,816 firms · 1,104 codes · ~7¢ · ~0.2s | **Verified in this repo** (see DEMO.md numbers table) |
| "Surat only" | True — don't imply national coverage yet |

---

## If YC asks follow-ups

Have these ready — short, no hedging:

**"Why hasn't someone done this?"**
> The registry is public but useless as-is — it's a list of names and activity codes.
> The work is turning an industry code into what a business actually makes and consumes,
> then joining those. That mapping is the asset.

**"Why would a business use it?"**
> Sourcing is the daily problem. Today it's word of mouth, so you get the supplier your cousin
> knows, not the best one three kilometres away. We make that searchable.

**"What's defensible?"**
> The material-level mapping and the corrections that accumulate on top of it. Every firm that
> confirms or fixes its own profile makes the graph better for everyone in that cluster.

**"How do you make money?"**
> [Your answer — leads/subscriptions/transactions. Decide before you record; don't improvise.]

**"What's fake / not built yet?"**
> Be upfront: GST verification isn't wired (the registry has no GSTIN), the data is Surat-only,
> and the supply links are inferred from registered activity — not confirmed transactions.
> Saying this *before* they find it buys more credibility than any feature.
