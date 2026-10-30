"""Which phone and email may be shown for a company: the firm's, never a person's.  0029.

A buyer who has found a supplier needs a way to reach it, and the company card used to
say "no phone on file" for 473 firms we held a phone for. But a contact is personal data
when the firm is one person. A sole trader's mobile on a CHEMEXCIL listing or their own
website is that person's mobile. So is "kunjal.sakariya@" at a limited company: it reaches
a named employee, not the firm.

What may reach Neo4j and the page depends on what the firm is:

  a proprietorship or HUF      nothing. A GSTIN with PAN holder type P or H is one person
                               (or one family), and so is everything that reaches them.
  a legal entity               its phone, and its email if that is a role mailbox. Legal
                               entity means a CIN; or a GSTIN whose holder type is not a
                               person (C company, F firm or LLP, T trust...); or a
                               registered company form in its own name (LIMITED, PVT, LTD,
                               LLP).
  anything else                a role mailbox only. A name with no company form proves
                               nothing either way: "SHARDA INDUSTRIES" may be a partnership
                               or one man, and a sole trader's phone is his own mobile.
                               "info@shardaindustries.com" reaches the business either way.

A role mailbox is info@, sales@, export@ and the like. A named person's address is theirs,
not the firm's, even at a limited company. Placeholders (info@example.com) are not
contacts at all.

Everything else stays in Postgres, where raw_company keeps it for the resolver's contact
match and for a supplier who later claims the profile. Pure functions; no store access.
"""
from __future__ import annotations

import re

from pipeline.gstin import is_personal, is_personal_pan

ENTITY_FORM = re.compile(r"\b(PRIVATE\s+LIMITED|LIMITED|PVT|LTD|LLP)\b", re.I)

_ROLE = (r"info|sales|export|exports|import|imports|contact|contactus|enquiry|enquiries|"
         r"inquiry|inquiries|marketing|admin|office|support|mail|business|purchase|"
         r"customercare|care|accounts|service|services|orders|order|hello|help|cs|cosec|"
         r"investor|investors|trade|corporate|connect|reach|query|queries|feedback|"
         r"market|mktg|compliance|ho|headoffice|customerservice|customerservices|"
         r"customersupport|quality|qa|technical|works|factory|plant|procurement|purchases|"
         r"dispatch|logistics|exim|team|desk|crm")
# A role word, optionally numbered or qualified by another role word or a region:
# "info@", "sales2@", "export.india@", "sales-export@". Not "sales.rajesh@".
ROLE_MAILBOX = re.compile(
    rf"^({_ROLE})(\d+|[._-]({_ROLE}|in|india|\d+))?@[a-z0-9.-]+\.[a-z]{{2,}}$", re.I)
PLACEHOLDER = re.compile(r"@(example|domain|email|yourdomain|yourcompany|test)\.", re.I)


def is_entity(legal_name: str | None, cin: str | None = None,
              gstin: str | None = None, pan: str | None = None) -> bool:
    """Is the firm a legal entity rather than one person?"""
    if is_personal(gstin) or is_personal_pan(pan):
        return False
    if cin or gstin:
        return True
    return bool(ENTITY_FORM.search(legal_name or ""))


def business_email(email: str | None) -> str | None:
    """The address if it is a role mailbox, else None."""
    e = (email or "").strip().lower()
    if not e or PLACEHOLDER.search(e) or not ROLE_MAILBOX.match(e):
        return None
    return e


def business_contacts(legal_name: str | None, cin: str | None, gstin: str | None,
                      pan: str | None, phone: str | None,
                      email: str | None) -> tuple[str | None, str | None]:
    """(phone, email) that may be shown. Either may be None."""
    if is_personal(gstin) or is_personal_pan(pan):
        return None, None
    mail = business_email(email)
    if not is_entity(legal_name, cin, gstin, pan):
        return None, mail
    digits = re.sub(r"\D", "", phone or "")
    return (phone if len(digits) >= 10 else None), mail
