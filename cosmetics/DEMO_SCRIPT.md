# Lattix — investor demo script

A 12-minute live demo for investors, built around the Indian MSME cosmetics supply chain.
Written 2026-10-31. Every question below was asked of the live demo that day, and every
number in the "On screen" lines is what it returned. `make demo-rehearse` re-asks all
twelve, in this order, through the same path the page uses. Run it the morning of the
pitch, and change a line here if a number has moved.

The technical walkthrough, with the caveat behind each answer, is `make demo`. This
document is the story you tell around it.

---

## The pitch in one line (memorise it)

> **Lattix is the sourcing brain for India's cosmetics supply chain.** Ask in English or
> Hinglish, and get the right manufacturers, ingredients, packaging and labs near you,
> with the evidence behind whether to trust each one.

The three things you are proving, live:
1. **It finds what directories can't.** Search by capability and distance across 1,31,663
   firms joined from 12 public sources, not by who paid for a listing.
2. **It tells you what is real.** Every firm carries an evidence grade, and every claim
   carries its source. It flags firms the government has struck off, and it says when it
   doesn't know.
3. **It speaks the market's language.** Hinglish questions get Hinglish answers. It
   understands clusters, pincodes and Indian regulation.

---

## 0. Pre-flight (30 minutes before)

| Check | How | Pass |
|---|---|---|
| The demo is up | `curl -s http://127.0.0.1:8099/healthz` | `"ok": true` |
| Every step still answers as scripted | `make demo-rehearse` (about 3 minutes) | `12/12 steps` |
| Routing still holds under paraphrase | `make demo-check` (about 5 minutes) | all pass |
| The page is warm | Open the site once; wait for the four figures under the headline | figures show |

- **Display:** light mode reads better on a projector (toggle at top right). Use browser
  zoom 110–125%. One window, no other tabs, notifications off.
- **Network:** keep a phone hotspot ready. Record one full rehearsal as a screen video:
  it is your fallback if the network or the model gateway fails mid-pitch.
- **Pace:** the demo allows 20 questions a minute per visitor and 2,000 a day. Don't
  re-ask a question to make it faster.
- **Where to read this:** the vault copy opens on a phone; keep it on a second screen.

---

## 1. Opening — the problem (90 seconds)

**Say:**

> India makes and sells a lot of cosmetics: the beauty and personal-care market is about
> **US$28–33 billion** [1]. Most new brands don't own factories. There are an estimated
> **800 to 1,200 D2C skincare brands** alone [2], and a single contract manufacturer can
> serve 50 of them [3].
>
> To launch one product, a founder has to find a manufacturer, ingredient suppliers,
> packaging and an accredited lab, and stay inside the Cosmetics Rules 2020. Today that
> happens over WhatsApp, at trade fairs and in directories where whoever pays the most
> ranks first.
>
> And the counterparty risk is real. GST officers found roughly **25,000 fake firms** in
> one year [4]. MSMEs are owed **₹7.3 lakh crore** in delayed payments [5]. The licences
> that say who may legally make cosmetics are issued by **state** licensing authorities,
> and no state publishes them in bulk [6].
>
> The supplier base is out there: over **7 crore** enterprises are on the Udyam MSME
> registry [7]. It just isn't searchable by what a buyer actually needs. That's what we
> built.

---

## 2. The demo — three acts (about 10 minutes)

Type each question exactly as written (copy from here), or click it where it's a
starter chip. Answers take 7–15 seconds, and the first one about 25. **Talk over the
stages:** they are the product.

### Act 1 — "Ananya launches a serum" (5 minutes)

*Illustrative persona: Ananya runs a two-year-old D2C skincare brand in Bengaluru and is
launching a niacinamide serum. She wants to manufacture in the Ahmedabad–Vapi belt.*

#### Step 1 — one question, a sourcing plan

**Type:** `I'm launching a niacinamide face serum in India. What do I need?`

**On screen (about 25 s):** the stages run one by one: *Checking the Cosmetics Rules
2020 → Looking up the BIS standards for the category → Finding suppliers of the
ingredient → …* Then a plan arrives:
- niacinamide is not on India's restricted list;
- skin care falls under the Ninth Schedule's BIS standards: **IS 15153, IS 3959,
  IS 10284, IS 6608**;
- 13 niacinamide suppliers, the most evidenced being Resonance Specialties;
- cosmetics makers and NABL-accredited labs.

**Say:** "One plain-English question, and it plans the whole launch: compliance,
ingredients, manufacturing, testing. Watch the stages. It isn't making this up. It
chooses **reviewed queries** over real registers, and you can see each one run."

**Click:** "How this was answered", under the reply. **Say:** "Every query it ran, and
how it understood each word. A compliance answer can only come from the regulator's own
data: that rule is enforced in code, not by prompt."

#### Step 2 — who can make it

**Type:** `contract manufacturers for skincare near Ahmedabad`

**On screen:** **19** firms in the Ahmedabad–Vapi–Ankleshwar cluster that advertise
contract or private-label manufacturing on their own websites, including Acticon Life
Sciences, Arise Enterprise and Accra Pac (Vapi). Each card has a website button.

**Click:** "Why this grade?" on any card.

**Say:** "Here's the honest part. This firm says on its own website that it does
private label, and the card says exactly that. The grade reads **Listed**, and the
drawer says why: the directory that lists it gives no address, so we haven't matched it
to a registry yet. A directory gives every listing the same green tick. We show you
what's actually evidenced."

**If asked about licences:** "State licence registers aren't public. We're filing RTIs
to get them, and when a buyer sends an RFQ through us, the licence is the first thing we
ask for."

#### Step 3 — packaging, ranked by distance

**Type:** `bottle suppliers near Ahmedabad` (or click the starter *bottle suppliers near
Baddi*)

**On screen:** 25 plastic-container makers registered on Udyam, about **12 km** from
Ahmedabad. The distance shows on every card ("· 12 km").

**Say:** "Freight and lead time decide margins for a small brand. So it ranks by
kilometres from where you are: a city, a district or a pincode. These come from the
government's MSME registry, so the grade reads **Registered (Udyam)**."

#### Step 4 — testing, and a side-by-side

**Type:** `NABL labs for cosmetics testing near Ahmedabad`

**On screen:** **6** labs with a live cosmetics scope on NABL's register, among them
SGS India, Accuprec Research Labs and Nivea's quality-control lab. Each shows its
certificate's validity date.

**Click:** **Compare** on three lab cards, then **Compare 3** in the tray.

**Say:** "Accreditation scope and validity come straight from NABL's register. If a
certificate has lapsed, it drops down the list and says so. And you can line labs up
side by side before you courier a sample."

#### Step 5 — the trust moment

**Type:** `tell me about Garuda Chemicals`

**On screen:** the card reads **Flagged**: "MCA status: Struck off, as of 2026-09-06".
The firm is still on an export council's member list and still has a live website.

**Say:** "This is the fake-firm problem in one card. Still on an export council's member
list, website still up, and the **Ministry of Corporate Affairs lists the company as
struck off**. We caught **683** of these automatically. A buyer paying an advance would
never know."

**Then type:** `tell me about Galaxy Surfactants`

**On screen:** **Found in 3 independent sources**: MCA (active), the CHEMEXCIL member
directory, and the Cosmetics Ingredients Expo 2025 exhibitor list.

**Say:** "Compare a company three independent sources agree on. We count independent
publishers, not listings. A firm can't buy its way up this grade."

> Say "MCA's register lists it as struck off", never "fraud" or "fake". It is a public
> record with a date, and that's all we claim.

### Act 2 — "Rakesh-bhai's glycerin is late" (2.5 minutes)

*Illustrative persona: Rakesh Patel runs a 40-person soap and personal-care unit in
Ahmedabad. His glycerin comes from a Mumbai trader and it's three weeks late. He wants
to export a face wash next year.*

#### Step 6 — Hinglish, by distance

**Type:** `mujhe Ahmedabad ke paas glycerin supplier chahiye`

**On screen:** the reply comes back **in Hinglish**. Latam Impex is about 22 km away,
Molkem Chemicals about 28 km (with phone and email), and Gokul Agro Resources about
37 km, in Gandhinagar.

**Say:** "Most of India's seven crore MSMEs don't search in English keywords. Rakesh asked
the way he talks, and got the three nearest glycerin suppliers, in his language, with
their phone numbers. His supplier is 500 km away. These are 22 to 37."

**Click:** the Molkem card, then **Website**, or show the phone and email in its drawer.

#### Step 7 — export-ready compliance

**Type:** `EU restrictions on salicylic acid`

**On screen:** EU Annex V entry 3. Maximum **0.5%** (as acid), not for children under
three, and an explicit note that EU rules have no force in India.

**Then type:** `is mercury allowed in cosmetics in India`

**On screen:** restricted, not banned. It's allowed only as a preservative in eye-area
products; elsewhere only as an unintentional trace. The answer cites **Rule 39(5)(a)
and (b)** of the Cosmetics Rules 2020.

**Say:** "Two jurisdictions, never mixed. It cites the exact rule, and it knows that an
EU restriction isn't Indian law. That's the difference between a compliance answer and
a chatbot guess."

### Act 3 — "The map nobody has" (2 minutes)

#### Step 8 — who makes for whom

**Type:** `who is Encube Ethicals linked to by a loan licence`

**On screen:** loan-licence links on CDSCO's register to **GlaxoSmithKline**,
**Glenmark** and **Sanofi India**, including one cosmetics loan licence (COS-9).

**Say:** "Who manufactures for whom is the most guarded fact in contract manufacturing.
This comes from the regulator's own register. And notice what it **refuses** to say:
the register names both firms but not which one owns the factory, so neither do we."

#### Step 9 — the market map

**Type:** `how many cosmetics firms are there in each cluster`

**On screen:** a live count by cluster: Palghar–Tarapur–Navi Mumbai **2,650**, Delhi NCR
**2,576**, Ahmedabad–Vapi–Ankleshwar **2,440**, Pune–Sangli–Nashik **2,310**, and so on.

**Say:** "That's a market map of 1.3 lakh firms, counted live, and it took one
sentence. Clusters, capabilities and relationships: the industry's map, which nobody
else has drawn."

#### Step 10 (if time) — regulatory foresight

**Type:** `EU banned ingredients still sold in India`

**On screen:** 20 ingredients that the EU regulates and that appear in products on
Indian shelves, led by titanium dioxide (31 products) and benzyl alcohol (25).

**Say:** "For a brand planning exports, this list is the reformulation work ahead of
them."

---

## 3. Close — what you're investing in (90 seconds)

**Say:**

> What you just saw is live, and built at almost no data cost:
> - **1,31,663 companies** from **12 public sources**, resolved into one graph, every fact
>   with its source;
> - **35,864 ingredients** and **2,445 rules**, India and EU, kept separate;
> - **191 accredited cosmetics labs**;
> - an evidence grade that counts independent publishers and flags struck-off companies;
> - distance ranking, and English and Hinglish.
>
> **Next, the loop that closes the deal:** select suppliers, send one RFQ, get quotes
> back with price, MOQ and lead time. That's the data no directory has, and every quote
> makes the graph smarter. Then GST verification at the moment money moves, supplier-
> claimed profiles, licence data through RTI, and WhatsApp.
>
> **Why now:** seven crore MSMEs have formalised on Udyam, D2C has made every founder a
> buyer, and language models finally make "ask it like you'd ask a person" work in
> Hinglish.
>
> **Why us:** an evidence graph that took 12 sources and a lot of entity resolution to
> build, and honesty as the product. It tells you what it doesn't know. In a market
> full of fake firms, that's the feature.
>
> *[Your ask: amount, use of funds, milestones.]*

---

## 4. Lines never to say

The product's claim is that it knows what it doesn't know. One overclaim in front of
investors undoes that.

| Don't say | Say instead | Why |
|---|---|---|
| "Verified suppliers" | "Evidence-graded suppliers" | No firm reaches G4 (GST verified) until a live GST provider is connected |
| "Licensed manufacturers" | "Registered makers"; "licence on file" only where a card says so | Two firms in the graph have licence evidence. State registers aren't public |
| "Fraud", "fake", "risky" about a named firm | "MCA's register lists it as struck off, as of <date>" | A public record with a date. Adjectives are a defamation risk |
| "Prices", "MOQ", "lead time" as if we hold them | "Those come from quotes, and the RFQ rail collects them" | Not in the graph today |
| "Delivers in X days" / "X km away" as a promise | "About X km, straight line between pincode centres" | Distance ranks; it doesn't promise. A quarter of pincodes have coarse coordinates |
| "Covers every supplier in India" | "1.3 lakh firms across the major cosmetics clusters" | The Udyam copy holds cosmetics and packaging codes, in 3,264 of 4,842 cluster pincodes |

---

## 5. If something goes wrong

| What happens | Do |
|---|---|
| An answer takes over 25 seconds | Narrate the stages. That's normal for the first, multi-part question |
| "Connection dropped — reconnecting…" | Keep talking. The answer keeps computing on the server, and the page picks it up where it stopped |
| "The connection dropped before the answer arrived" | Click **Try again** under it |
| "That is more than 20 questions in a minute" | Wait 60 seconds; talk through the last answer meanwhile |
| "The demo has answered its budget of questions for today" | Switch to the recorded video. The cap (2,000 a day) resets at midnight |
| "The graph did not answer", or the page hangs | The model gateway is down. Switch to the video. (Gap: the demo has no model-free fallback yet. It's in IDEAS.md) |
| An odd firm name in a list, like "APEX ELECTRONICS" among cosmetics makers | "Udyam is self-declared: a firm registers its own activities. We show what the register says and grade it, which is why the grade exists" |
| A list where every card reads "Listed" | "Those reached us through a directory with no address. That's a gap in our data, not a finding about the firm. The MCA bridge and the next Udyam sweep fix it" |
| A question returns nothing | Read the reason aloud. It always says what's missing and what would answer it. That's the honesty moment |

Questions that are safe to improvise: anything from the starter chips; "… near
`<pincode>`"; "tell me about `<company>`"; "where can I test `<product>`"; "is `<ingredient>`
restricted in the EU". Avoid places that geocode badly (Palghar, Tarapur) and "alternatives
to …" (the substitutes data is thin).

---

## 6. Investor Q&A prep

**"How is this different from IndiaMART or TradeIndia?"**
They are listings sellers pay for, and ranking follows ad spend. We start from the
buyer's need and from public registers: Udyam, MCA, CDSCO, NABL, the Cosmetics Rules,
EU CosIng. We rank by fit and distance, and show the evidence behind every firm. We don't
scrape marketplaces.

**"It's all public data. Where's the moat?"**
Public data is the cold start, not the moat. The moat has three layers:
1. **The resolved evidence graph:** 12 sources, 2.3 lakh crosswalk decisions, an evidence
   grade per firm. That is months of entity resolution.
2. **First-party data from the RFQ loop:** quotes, MOQs, response times, outcomes. No
   one can download that.
3. **Supplier-claimed, GST-verified profiles:** they compound as both sides join.

**"How accurate is it?"**
Every answer comes from a reviewed query; the model never writes the compliance logic.
Facts carry a tier: regulator, company-declared, observed, inferred. Only tier 1 may
answer a compliance question, and that is enforced in code. 285 automated checks run on
every build, and the demo questions are re-asked through the live product before every
pitch. Ingredient-supply claims scraped from company catalogues measured about **83%**
precise on a uniform 100-row sample, and the cards label them as the company's own claim.

**"Most of your firms are just 'Registered' or 'Listed'. Isn't the trust layer thin?"**
Yes, and we show it rather than hide it. 99.7% of firms appear in only one independent
source, because India's registries don't talk to each other. The fixes are already built
or queued:
- the MCA bridge for about 14,000 company names;
- a national Udyam re-sweep;
- ZED and pollution-board registers;
- live GST verification at RFQ time;
- supplier-claimed profiles.

**"What does a question cost?"**
Data cost so far is ₹0: all public sources. Routing costs about $0.002 a question.
The answer is written by a Sonnet-class model with prompt caching. *Measure the
per-question cost from the gateway before quoting a number; it isn't tracked per
request yet.*

**"What about DPDP and privacy?"**
- We show business contacts only: never a sole trader's own number, and no named people.
- A proprietor's PAN, embedded in their GSTIN, is masked everywhere.
- No personal data enters the graph that the AI can read.
- GST filing history is never shown publicly.
- The DPDP Rules' main obligations phase in around May 2027, and we're building to them now.

**"Why cosmetics first?"**
- It's regulated, so evidence matters more than listings.
- Sourcing is bill-of-materials heavy: ingredients, packaging, a manufacturer and a lab
  for every launch.
- D2C has made thousands of founders into first-time buyers.

The engine isn't cosmetics-specific. The same Udyam pipeline already runs a textiles
graph, and food (FSSAI) and pharma are natural next verticals.

**"How do you reach customers?"**
- **Demand side:** D2C founder communities and incubators, contract manufacturers (each
  is also a big buyer of ingredients and packaging), and trade fairs such as Cosmoprof
  India and HPCI.
- **Supply side:** cluster associations, and a free "claim your profile" for the
  1.3 lakh firms already in the graph.

**"How do you make money?"**
- **Brands:** freemium search, plus paid procurement seats for teams.
- **Suppliers:** verified profiles, plus qualified-lead subscriptions.
- **Transactions:** a fee on RFQs that convert to orders.
- **Data:** a supplier-verification API for brands onboarding vendors.

---

## Appendix A — the twelve steps at a glance

Measured on the live demo, 2026-10-31. `make demo-rehearse` re-checks every row.

| # | Question | Reviewed query | Key result | Time |
|---|---|---|---|---|
| 1 | I'm launching a niacinamide face serum in India. What do I need? | standards, suppliers, makers, labs | IS 15153 / 3959 / 10284 / 6608; 13 niacinamide suppliers | ~25 s |
| 2 | contract manufacturers for skincare near Ahmedabad | third-party manufacturers | 19 in the Ahmedabad–Vapi cluster | ~13 s |
| 3 | bottle suppliers near Ahmedabad | packaging by distance | 25 within ~12 km | ~8 s |
| 4 | NABL labs for cosmetics testing near Ahmedabad | NABL register | 6 in-date labs incl. SGS India | ~9 s |
| 5 | tell me about Garuda Chemicals | company profile | Flagged: MCA struck off | ~8 s |
| 6 | tell me about Galaxy Surfactants | company profile | 3 independent sources | ~13 s |
| 7 | mujhe Ahmedabad ke paas glycerin supplier chahiye | ingredients by distance | Hinglish reply; 22 / 28 / 37 km | ~9 s |
| 8 | EU restrictions on salicylic acid | EU annexes | Annex V/3, max 0.5% | ~7 s |
| 9 | is mercury allowed in cosmetics in India | Cosmetics Rules 2020 | Rule 39(5)(a)(b) | ~7 s |
| 10 | who is Encube Ethicals linked to by a loan licence | CDSCO loan-licence register | GSK, Glenmark, Sanofi | ~7 s |
| 11 | how many cosmetics firms are there in each cluster | a counted query | 2,650 / 2,576 / 2,440 … | ~15 s |
| 12 | EU banned ingredients still sold in India | EU rules × Indian shelves | 20 ingredients | ~10 s |

## Appendix B — sources for the opening

Press coverage unless noted. Check the primary release before putting a number on a slide.

1. Beauty and personal-care market, US$28–33 billion: [Ken Research](https://www.kenresearch.com/india-beauty-and-personal-care-market)
   (US$28 bn, 2025); Statista via [India Briefing](https://www.india-briefing.com/news/india-beauty-personal-care-strategic-insights-39518.html) (US$33.08 bn, 2025).
2. 800–1,200 active D2C skincare brands, a rough industry estimate: [Clickpost](https://www.clickpost.ai/blog/d2c-brands-in-india/skin-care).
3. One contract manufacturer serving 50+ brands (Naturis Cosmetics):
   [D2C Insider](https://pulse.d2cinsider.com/naturis-cosmetics-raises-e2-82-b933-74-crore-to-power-indias-fast-growing-d2c-beauty-ecosystem/).
4. ~25,000 fake GST firms and ₹61,545 crore of fake input-tax credit in FY 2024-25;
   ~1,200 a month in 2025: [Outlook Business](https://www.outlookbusiness.com/news/gst-officers-detect-rs-15851-cr-fraudulent-itc-claims-in-april-june-3558-fake-firms-uncovered).
5. ₹7.34 lakh crore of delayed payments to MSMEs (March 2024):
   [GAME / FISME report](https://fisme.org.in/study/delayed-payments-to-msmes-decline-from-rs-10-lakh-cr-to-rs-7-lakh-cr-but-challenges-persist-game-fisme-report/);
   a 73-day average wait (Recordent, 2026): [MediaBrief](https://mediabrief.com/recordent-report-highlights-working-capital-stress-for-indian-smes-as-average-overdue-receivables-cross-%E2%82%B93-83-crore/).
6. COS-8 and COS-9 (loan) licences are issued by state licensing authorities:
   [CliniExperts](https://cliniexperts.com/cosmetic-manufacturing-license-in-india-the-state-licensing-authority-sla-process-explained/);
   the Delhi Drugs Control fee schedule ([PDF](https://drugs.delhi.gov.in/sites/default/files/2026-03/formsfeesmanufacturinglicensescosmetics.pdf)).
7. Udyam registrations past 7.3 crore (December 2025): [MSME ministry year-end review](https://www.aviation-defence-universe.com/year-end-review-2025-ministry-of-micro-small-medium-enterprises/);
   over 7.9 crore (May 2026): [IANS](https://ianslive.in/over-79-crore-enterprises-register-through-udyam-platforms--20260514141009).
