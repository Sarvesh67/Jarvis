# 0029 — The card shows a firm's business contacts, never a person's

**Status** accepted 2026-10-30 · **Affects** `pipeline/contact.py`, `load_neo4j.load_companies`,
vendor templates, `serve/index.html`, s01_cdsco, s14_sites

## Context
A buyer who has found a supplier has to reach it. The company card said "no phone on
file" for every firm, including the 473 we held a phone for and the 975 we held an email
for. Those values were already on Company nodes, unfiltered, where the agent's own Cypher
could read them. The agent's skill said the opposite: that contacts are held outside the
graph on purpose.

Under DPDP a contact is personal data when it reaches a person. A sole trader's mobile on
a CHEMEXCIL listing or their own website is that person's mobile. "kunjal.sakariya@" at a
limited company reaches a named employee, not the firm. TRUST_PLAN §7.3 already lists a
proprietor's phone and email as personal.

## What the data actually says
Measured over the 1,095 Company nodes carrying a contact on 2026-10-30:
- Legal form is known for only 119 of them by CIN or GSTIN, 4 of those proprietorships.
  A company form in the registered name (Ltd, Pvt Ltd, LLP) covers 765.
- Phones are almost all 10-digit mobiles: 440 of 473.
- Of 975 emails, 618 are role mailboxes (info@, sales@, export@). The rest are named
  people, personal accounts (gmail, yahoo) and placeholders ("info@example.com").

## Decision
Sarvesh chose "business contacts only". What reaches Neo4j, and so the page and the
agent, depends on what the firm is:

| The firm | Shown |
|---|---|
| Proprietorship or HUF (GSTIN holder type P or H) | nothing |
| Legal entity (a CIN, a non-personal GSTIN, or a company form in its name) | phone, and email if it is a role mailbox |
| Form unknown | a role mailbox only |

A firm of unknown form gets no phone because "Sharda Industries" may be one man, and then
the phone is his mobile. Its "info@shardaindustries.com" reaches the business either way.
A named person's address is never shown, not even at a limited company.

Everything withheld stays in Postgres `raw_company`. There the resolver still uses it as
a contact match, and a supplier who claims the profile can confirm it.

## Consequence
810 firms show a business contact: 294 with a phone, 655 with a role mailbox. The card
gives Call and Email buttons, and says that a sole trader's number and a named person's
address are not shown. Where we hold no business contact, it says exactly that, not "no
phone on file".

`load_companies` writes the filtered values and nulls the rest, which clears contacts
loaded before this rule. The `no_contact_for_a_sole_trader` golden query and
`test_graph_contacts_are_business_lines` hold the line. The allowlist of role words can
only err towards hiding a business mailbox, never towards showing a person.
