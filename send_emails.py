#!/usr/bin/env python3
"""
Email Sender — reads contacts.csv, sends next N emails via SMTP, marks them sent.
Designed to run on a schedule (GitHub Actions every 4 hours).

Contact CSV columns: first_name, last_name, email, company, title, sent_at
- sent_at empty = not yet sent
- sent_at filled = already sent, skip

Usage:
  python send_emails.py              # sends 10 emails (default)
  python send_emails.py --batch 20   # sends 20 emails
  python send_emails.py --dry-run    # shows who would be emailed, sends nothing
"""

import os
import csv
import smtplib
import argparse
import traceback
from pathlib import Path
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ── Config (from environment variables / GitHub Secrets) ──────────────────────

SMTP_HOST     = os.getenv("SMTP_HOST",     "mail.futureedge-consulting.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
FROM_EMAIL    = os.getenv("FROM_EMAIL",    "mohsin.bhatti@futureedge-consulting.com")
EMAIL_PASS    = os.getenv("EMAIL_PASSWORD", "")
SENDER_NAME   = os.getenv("SENDER_NAME",   "Mohsin")
BATCH_SIZE    = int(os.getenv("BATCH_SIZE", "10"))

DIR           = Path(__file__).parent
CONTACTS_FILE = DIR / "contacts.csv"
TEMPLATE_FILE = DIR / "email_template.txt"

DEFAULT_SUBJECT = "Quick question for {first_name}"

DEFAULT_BODY = """\
Hi {first_name},

I came across {company_name} and wanted to reach out directly.

I run Future Edge Consulting — we help businesses leverage AI and smart \
systems to grow faster and reduce overhead. I think there could be a \
genuinely useful fit with what you're doing at {company_name}.

Would you be open to a 15-minute call this week? Happy to work around your schedule.

Best,
{sender_name}
Future Edge Consulting
mohsin.bhatti@futureedge-consulting.com"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_template() -> str:
    if TEMPLATE_FILE.exists():
        return TEMPLATE_FILE.read_text().strip()
    return DEFAULT_BODY


def render(template: str, contact: dict) -> str:
    return template.format(
        first_name=contact.get("first_name") or "there",
        last_name=contact.get("last_name", ""),
        company_name=contact.get("company") or "your company",
        title=contact.get("title", ""),
        sender_name=SENDER_NAME,
    )


def load_contacts() -> list[dict]:
    if not CONTACTS_FILE.exists():
        raise FileNotFoundError(f"contacts.csv not found at {CONTACTS_FILE}")
    with open(CONTACTS_FILE, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def save_contacts(contacts: list[dict]):
    if not contacts:
        return
    fieldnames = list(contacts[0].keys())
    # Ensure sent_at column exists
    if "sent_at" not in fieldnames:
        fieldnames.append("sent_at")
    with open(CONTACTS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(contacts)


def send_email(smtp: smtplib.SMTP, to_email: str, subject: str, body: str):
    msg = MIMEMultipart("alternative")
    msg["From"]    = f"{SENDER_NAME} <{FROM_EMAIL}>"
    msg["To"]      = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    smtp.sendmail(FROM_EMAIL, to_email, msg.as_string())


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch",   type=int, default=BATCH_SIZE, help="Emails to send this run")
    parser.add_argument("--dry-run", action="store_true",          help="Preview only, send nothing")
    args = parser.parse_args()

    if not EMAIL_PASS and not args.dry_run:
        raise SystemExit("❌  EMAIL_PASSWORD environment variable not set.")

    body_template = load_template()
    contacts      = load_contacts()

    # Find unsent contacts
    unsent = [c for c in contacts if not c.get("sent_at", "").strip()]
    batch  = unsent[: args.batch]

    total_sent  = sum(1 for c in contacts if c.get("sent_at", "").strip())
    total_left  = len(unsent)

    print(f"\n{'─'*55}")
    print(f"  Total contacts : {len(contacts)}")
    print(f"  Already sent   : {total_sent}")
    print(f"  Remaining      : {total_left}")
    print(f"  This batch     : {len(batch)}")
    print(f"  Dry run        : {args.dry_run}")
    print(f"{'─'*55}\n")

    if not batch:
        print("✅  All contacts have been emailed. Campaign complete!")
        return

    sent_count  = 0
    fail_count  = 0
    sent_emails = set()

    smtp_conn = None
    if not args.dry_run:
        smtp_conn = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        smtp_conn.ehlo()
        smtp_conn.starttls()
        smtp_conn.ehlo()
        smtp_conn.login(FROM_EMAIL, EMAIL_PASS)

    try:
        for contact in batch:
            to_email = (contact.get("email") or "").strip()
            name     = f"{contact.get('first_name','')} {contact.get('last_name','')}".strip()
            company  = contact.get("company", "") or "unknown"

            if not to_email:
                print(f"  SKIP  {name:<28} — no email address")
                contact["sent_at"] = "SKIPPED"
                continue

            if to_email in sent_emails:
                print(f"  SKIP  {name:<28} — duplicate email")
                contact["sent_at"] = "DUPLICATE"
                continue

            subject = render(DEFAULT_SUBJECT, contact)
            body    = render(body_template, contact)

            if args.dry_run:
                print(f"  DRY   {name:<28} → {to_email}  ({company})")
                sent_count += 1
                sent_emails.add(to_email)
                continue

            try:
                send_email(smtp_conn, to_email, subject, body)
                contact["sent_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                sent_emails.add(to_email)
                sent_count += 1
                print(f"  SENT  {name:<28} → {to_email}")
            except Exception as e:
                fail_count += 1
                print(f"  FAIL  {name:<28} → {to_email}  ({e})")

    finally:
        if smtp_conn:
            smtp_conn.quit()

    # Save progress back to CSV (so next run skips these)
    if not args.dry_run:
        save_contacts(contacts)

    print(f"\n{'─'*55}")
    print(f"  Sent this run  : {sent_count}")
    if fail_count:
        print(f"  Failed         : {fail_count}")
    remaining_after = total_left - sent_count
    if remaining_after > 0:
        hours_left = (remaining_after / args.batch) * 4
        print(f"  Still to go    : {remaining_after} contacts (~{hours_left:.0f} hours at current pace)")
    else:
        print(f"  Campaign complete!")
    print(f"{'─'*55}\n")


if __name__ == "__main__":
    main()
