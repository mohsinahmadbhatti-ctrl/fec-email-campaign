#!/usr/bin/env python3
"""
Email Sender — reads contacts.csv, sends next N emails via SMTP, marks them sent.
Copies each sent email to IMAP Sent folder (visible in Outlook).
Sends a batch summary to Mohsin after every run.
Designed to run on a schedule (GitHub Actions every 4 hours).
"""

import os
import csv
import time
import imaplib
import smtplib
import argparse
import email.utils
from pathlib import Path
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ── Config ────────────────────────────────────────────────────────────────────

SMTP_HOST   = os.getenv("SMTP_HOST",     "mail.futureedge-consulting.com")
SMTP_PORT   = int(os.getenv("SMTP_PORT", "465"))
IMAP_HOST   = os.getenv("IMAP_HOST",     "mail.futureedge-consulting.com")
IMAP_PORT   = int(os.getenv("IMAP_PORT", "993"))
FROM_EMAIL  = os.getenv("FROM_EMAIL",    "mohsin.bhatti@futureedge-consulting.com")
EMAIL_PASS  = os.getenv("EMAIL_PASSWORD", "")
SENDER_NAME = os.getenv("SENDER_NAME",   "Mohsin")
BATCH_SIZE  = int(os.getenv("BATCH_SIZE", "10"))

DIR           = Path(__file__).parent
CONTACTS_FILE = DIR / "contacts.csv"
TEMPLATE_FILE = DIR / "email_template.txt"

DEFAULT_SUBJECT = "Quick thought on {company_name}"

DEFAULT_BODY = """\
Hi {first_name},

In your role at {company_name}, you're likely navigating the same pressures we \
hear consistently from senior leaders across the region — HR systems that don't \
scale cleanly, talent decisions made without the right data, and capability gaps \
that create drag across the business.

Future Edge Consulting works with organisations across the GCC and South Asia on \
exactly these areas: talent and psychometric frameworks that bring rigour to people \
decisions, ERP implementation that removes the friction holding operations back, and \
capability development programmes that actually change how teams perform.

Our work tends to land best with organisations that have outgrown their current \
approach and are ready to build something more deliberate. If any of this connects \
to what's on your plate, I'd welcome a brief conversation.

Mohsin Ahmad Bhatti
Founder & Director | Future Edge Consulting Pvt Ltd
P: +92 307 2202237   M: +44 746 3987384
W: www.futureedge-consulting.com
E: mohsin.bhatti@futureedge-consulting.com
A: 7th Floor, Executive Tower, Dolmen Mall, Clifton, Karachi, Pakistan"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_template() -> tuple[str, str]:
    if TEMPLATE_FILE.exists():
        text = TEMPLATE_FILE.read_text().strip()
        lines = text.splitlines()
        if lines and lines[0].lower().startswith("subject:"):
            subject = lines[0].split(":", 1)[1].strip()
            body = "\n".join(lines[2:]).strip()
            return subject, body
        return DEFAULT_SUBJECT, text
    return DEFAULT_SUBJECT, DEFAULT_BODY


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
    if "sent_at" not in fieldnames:
        fieldnames.append("sent_at")
    with open(CONTACTS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(contacts)


def build_message(to_email: str, subject: str, body: str) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["From"]       = f"{SENDER_NAME} <{FROM_EMAIL}>"
    msg["To"]         = to_email
    msg["Subject"]    = subject
    msg["Date"]       = email.utils.formatdate(localtime=False)
    msg["Message-ID"] = email.utils.make_msgid(domain=FROM_EMAIL.split("@")[1])
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


def find_imap_folder(imap: imaplib.IMAP4_SSL, keyword: str) -> str:
    _, folder_list = imap.list()
    for entry in folder_list:
        decoded = entry.decode() if isinstance(entry, bytes) else entry
        if keyword.lower() in decoded.lower():
            return decoded.rsplit(" ", 1)[-1].strip().strip('"')
    return keyword.capitalize()


def copy_to_sent(imap: imaplib.IMAP4_SSL, sent_folder: str, msg: MIMEMultipart):
    imap.append(
        sent_folder,
        r"(\Seen)",
        imaplib.Time2Internaldate(time.time()),
        msg.as_bytes(),
    )


def send_summary(smtp: smtplib.SMTP, sent_count: int, fail_count: int,
                 total_done: int, total_contacts: int, log_lines: list[str]):
    remaining = total_contacts - total_done
    days_left  = remaining / (BATCH_SIZE * 6) if BATCH_SIZE else 0

    subject = f"Campaign update: {sent_count} emails sent — {total_done}/{total_contacts} total"

    body = f"""Campaign batch complete — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

Batch summary
─────────────────────────────
Sent this run   : {sent_count}
Failed          : {fail_count}
─────────────────────────────
Total sent      : {total_done}
Remaining       : {remaining}
Est. completion : ~{days_left:.0f} days at current pace

Emails sent this batch:
"""
    for line in log_lines:
        body += f"  {line}\n"

    if fail_count:
        body += f"\n⚠️  {fail_count} email(s) failed — check GitHub Actions logs for details."

    if remaining == 0:
        body += "\n\n🎉 Campaign complete — all contacts have been emailed."

    msg = build_message(FROM_EMAIL, subject, body)
    smtp.sendmail(FROM_EMAIL, FROM_EMAIL, msg.as_string())
    print(f"  Summary email sent to {FROM_EMAIL}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch",   type=int, default=BATCH_SIZE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not EMAIL_PASS and not args.dry_run:
        raise SystemExit("❌  EMAIL_PASSWORD not set.")

    subject_template, body_template = load_template()
    contacts = load_contacts()

    unsent     = [c for c in contacts if not c.get("sent_at", "").strip()]
    batch      = unsent[: args.batch]
    total_sent = sum(1 for c in contacts if c.get("sent_at", "").strip())

    print(f"\n{'─'*55}")
    print(f"  Total contacts : {len(contacts)}")
    print(f"  Already sent   : {total_sent}")
    print(f"  Remaining      : {len(unsent)}")
    print(f"  This batch     : {len(batch)}")
    print(f"  Dry run        : {args.dry_run}")
    print(f"{'─'*55}\n")

    if not batch:
        print("✅  All contacts emailed. Campaign complete!")
        return

    sent_count  = 0
    fail_count  = 0
    sent_emails = set()
    log_lines   = []

    if args.dry_run:
        for contact in batch:
            to_email = (contact.get("email") or "").strip()
            name     = f"{contact.get('first_name','')} {contact.get('last_name','')}".strip()
            company  = contact.get("company", "") or "unknown"
            print(f"  DRY   {name:<28} → {to_email}  ({company})")
            sent_count += 1
        print(f"\n{'─'*55}")
        print(f"  Would send: {sent_count}")
        print(f"{'─'*55}\n")
        return

    # Connect SMTP
    smtp = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
    smtp.login(FROM_EMAIL, EMAIL_PASS)

    # Connect IMAP (for Sent folder copy)
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    imap.login(FROM_EMAIL, EMAIL_PASS)
    sent_folder = find_imap_folder(imap, "sent")
    print(f"  IMAP Sent folder: {sent_folder}\n")

    try:
        for contact in batch:
            to_email = (contact.get("email") or "").strip()
            name     = f"{contact.get('first_name','')} {contact.get('last_name','')}".strip()
            company  = contact.get("company", "") or "unknown"

            if not to_email:
                contact["sent_at"] = "SKIPPED"
                continue

            if to_email in sent_emails:
                contact["sent_at"] = "DUPLICATE"
                continue

            subject = render(subject_template, contact)
            body    = render(body_template, contact)
            msg     = build_message(to_email, subject, body)

            try:
                smtp.sendmail(FROM_EMAIL, to_email, msg.as_string())
                copy_to_sent(imap, sent_folder, msg)         # → appears in Outlook Sent
                contact["sent_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                sent_emails.add(to_email)
                sent_count += 1
                log_lines.append(f"{name} <{to_email}> ({company})")
                print(f"  SENT  {name:<28} → {to_email}")
            except Exception as e:
                fail_count += 1
                print(f"  FAIL  {name:<28} → {to_email}  ({e})")

    finally:
        smtp.quit()
        imap.logout()

    # Save progress
    save_contacts(contacts)

    # Send summary to Mohsin
    total_done_now = total_sent + sent_count
    smtp2 = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
    smtp2.login(FROM_EMAIL, EMAIL_PASS)
    send_summary(smtp2, sent_count, fail_count, total_done_now, len(contacts), log_lines)
    smtp2.quit()

    print(f"\n{'─'*55}")
    print(f"  Sent           : {sent_count}")
    if fail_count:
        print(f"  Failed         : {fail_count}")
    print(f"  Total done     : {total_done_now}/{len(contacts)}")
    print(f"{'─'*55}\n")


if __name__ == "__main__":
    main()
