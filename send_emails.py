#!/usr/bin/env python3
"""
Email Sender — 3-email drip sequence.

Each contact receives up to 3 emails:
  Email 1: Day 0   — short intro
  Email 2: Day 3+  — follow-up with value prop
  Email 3: Day 8+  — breakup

Reads contacts.csv, determines who is due for their next email,
sends a batch via SMTP, copies to IMAP Sent folder, and sends
a summary to Mohsin after every run.

Triggered hourly by GitHub Actions (via cron-job.org).
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

SMTP_HOST   = os.getenv("SMTP_HOST",     "smtp-relay.brevo.com")
SMTP_PORT   = int(os.getenv("SMTP_PORT", "587"))
SMTP_LOGIN  = os.getenv("SMTP_LOGIN",    "")
SMTP_PASS   = os.getenv("SMTP_PASSWORD", "")
IMAP_HOST   = os.getenv("IMAP_HOST",     "mail.futureedge-consulting.com")
IMAP_PORT   = int(os.getenv("IMAP_PORT", "993"))
FROM_EMAIL  = os.getenv("FROM_EMAIL",    "mohsin.bhatti@futureedge-consulting.com")
EMAIL_PASS  = os.getenv("EMAIL_PASSWORD", "")
SENDER_NAME = os.getenv("SENDER_NAME",   "Mohsin")
BATCH_SIZE  = int(os.getenv("BATCH_SIZE", "10"))

DIR           = Path(__file__).parent
CONTACTS_FILE = DIR / "contacts.csv"

TEMPLATE_FILES = {
    1: DIR / "email_template_1.txt",
    2: DIR / "email_template_2.txt",
    3: DIR / "email_template_3.txt",
}

DELAY_DAYS = {2: 3, 3: 5}


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_templates() -> dict[int, tuple[str, str]]:
    templates = {}
    for num, path in TEMPLATE_FILES.items():
        if not path.exists():
            raise FileNotFoundError(f"Template {path} not found")
        text = path.read_text().strip()
        lines = text.splitlines()
        if lines[0].lower().startswith("subject:"):
            subject = lines[0].split(":", 1)[1].strip()
            body = "\n".join(lines[2:]).strip()
        else:
            subject = f"Following up — {{company_name}}"
            body = text
        templates[num] = (subject, body)
    return templates


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
    with open(CONTACTS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(contacts)


def parse_ts(s: str):
    s = s.strip()
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)


def get_next_email(contact) -> int | None:
    """Return which email (1/2/3) to send next, or None if done or not yet due."""
    now = datetime.now(timezone.utc)

    e1 = parse_ts(contact.get("email1_sent_at", ""))
    e2 = parse_ts(contact.get("email2_sent_at", ""))
    e3 = parse_ts(contact.get("email3_sent_at", ""))

    if not e1:
        return 1

    if e3:
        return None

    if not e2:
        if (now - e1).total_seconds() >= DELAY_DAYS[2] * 86400:
            return 2
        return None

    if (now - e2).total_seconds() >= DELAY_DAYS[3] * 86400:
        return 3
    return None


def build_message(to_email: str, subject: str, body: str,
                  in_reply_to: str = None) -> MIMEMultipart:
    msg = MIMEMultipart("mixed")
    msg["From"]       = f"{SENDER_NAME} <{FROM_EMAIL}>"
    msg["To"]         = to_email
    msg["Subject"]    = subject
    msg["Date"]       = email.utils.formatdate(localtime=False)
    msg["Message-ID"] = email.utils.make_msgid(domain=FROM_EMAIL.split("@")[1])

    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"]  = in_reply_to

    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


def connect_smtp() -> smtplib.SMTP:
    last_err = None
    for port in (SMTP_PORT, 2525):
        try:
            smtp = smtplib.SMTP(SMTP_HOST, port, timeout=25)
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(SMTP_LOGIN, SMTP_PASS)
            print(f"  SMTP connected: {SMTP_HOST}:{port}")
            return smtp
        except Exception as e:
            last_err = e
            print(f"  SMTP {SMTP_HOST}:{port} failed: {e}")
            time.sleep(5)
    raise last_err


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


def send_summary(smtp: smtplib.SMTP, counts: dict, total_progress: dict,
                 total_contacts: int, log_lines: list[str]):
    e1 = counts.get(1, 0)
    e2 = counts.get(2, 0)
    e3 = counts.get(3, 0)
    fails = counts.get("fail", 0)
    sent_total = e1 + e2 + e3

    done_e1  = total_progress["email1"]
    done_all = total_progress["completed"]

    subject = f"Campaign update: {sent_total} sent — {done_e1}/{total_contacts} reached"

    body = f"""Campaign batch complete — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

Batch summary
─────────────────────────────
Email 1 (intro)     : {e1}
Email 2 (follow-up) : {e2}
Email 3 (breakup)   : {e3}
Failed              : {fails}
─────────────────────────────
Contacts reached    : {done_e1}/{total_contacts}
Sequence complete   : {done_all}

Emails sent this batch:
"""
    for line in log_lines:
        body += f"  {line}\n"

    if fails:
        body += f"\n⚠️  {fails} email(s) failed — check GitHub Actions logs."

    if done_all == total_contacts:
        body += "\n\n🎉 Campaign complete — all contacts finished the sequence."

    msg = build_message(FROM_EMAIL, subject, body)
    smtp.sendmail(FROM_EMAIL, FROM_EMAIL, msg.as_string())
    print(f"  Summary email sent to {FROM_EMAIL}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch",   type=int, default=BATCH_SIZE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.dry_run and not (SMTP_LOGIN and SMTP_PASS):
        raise SystemExit("❌  SMTP_LOGIN / SMTP_PASSWORD not set.")

    templates = load_templates()
    contacts  = load_contacts()

    # Find contacts due for their next email
    due = []
    for contact in contacts:
        to_email = (contact.get("email") or "").strip()
        if not to_email:
            continue
        next_num = get_next_email(contact)
        if next_num:
            due.append((contact, next_num))

    # Prioritise follow-ups (warmer leads) over new intros
    due.sort(key=lambda x: (x[1] == 1, x[1]))
    batch = due[: args.batch]

    # Stats
    e1_done  = sum(1 for c in contacts if parse_ts(c.get("email1_sent_at", "")))
    all_done = sum(1 for c in contacts if parse_ts(c.get("email3_sent_at", "")))
    due_e1   = sum(1 for _, n in due if n == 1)
    due_e2   = sum(1 for _, n in due if n == 2)
    due_e3   = sum(1 for _, n in due if n == 3)

    print(f"\n{'─'*55}")
    print(f"  Total contacts    : {len(contacts)}")
    print(f"  Reached (email 1) : {e1_done}")
    print(f"  Sequence complete : {all_done}")
    print(f"  Due now  E1/E2/E3 : {due_e1}/{due_e2}/{due_e3}")
    print(f"  This batch        : {len(batch)}")
    print(f"  Dry run           : {args.dry_run}")
    print(f"{'─'*55}\n")

    if not batch:
        print("✅  No emails due right now.")
        return

    if args.dry_run:
        for contact, email_num in batch:
            to_email = (contact.get("email") or "").strip()
            name = f"{contact.get('first_name','')} {contact.get('last_name','')}".strip()
            company = contact.get("company", "") or "unknown"
            print(f"  DRY  E{email_num}  {name:<28} → {to_email}  ({company})")
        return

    # Connect SMTP
    smtp = connect_smtp()

    # Connect IMAP (best-effort)
    imap = None
    sent_folder = None
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=25)
        imap.login(FROM_EMAIL, EMAIL_PASS)
        sent_folder = find_imap_folder(imap, "sent")
        print(f"  IMAP Sent folder: {sent_folder}\n")
    except Exception as e:
        print(f"  IMAP unavailable ({e}) — sending anyway\n")
        imap = None

    counts    = {"fail": 0}
    log_lines = []
    sent_emails_this_run = set()

    try:
        for contact, email_num in batch:
            to_email = (contact.get("email") or "").strip()
            name     = f"{contact.get('first_name','')} {contact.get('last_name','')}".strip()
            company  = contact.get("company", "") or "unknown"

            if to_email in sent_emails_this_run:
                continue

            subj_tpl, body_tpl = templates[email_num]
            subject = render(subj_tpl, contact)
            body    = render(body_tpl, contact)

            in_reply_to = contact.get("msg_id", "").strip() or None
            msg = build_message(to_email, subject, body,
                                in_reply_to if email_num > 1 else None)

            try:
                smtp.sendmail(FROM_EMAIL, to_email, msg.as_string())
                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                contact[f"email{email_num}_sent_at"] = now_str
                if email_num == 1:
                    contact["msg_id"] = msg["Message-ID"]
                sent_emails_this_run.add(to_email)
                counts[email_num] = counts.get(email_num, 0) + 1
                log_lines.append(f"E{email_num}  {name} <{to_email}> ({company})")
                print(f"  SENT E{email_num}  {name:<28} → {to_email}")
            except Exception as e:
                counts["fail"] += 1
                print(f"  FAIL E{email_num}  {name:<28} → {to_email}  ({e})")
                continue

            if imap is not None:
                try:
                    copy_to_sent(imap, sent_folder, msg)
                except Exception as e:
                    print(f"        (Sent-folder copy failed: {e})")

    finally:
        smtp.quit()
        if imap is not None:
            try:
                imap.logout()
            except Exception:
                pass

    save_contacts(contacts)

    # Send summary
    total_progress = {
        "email1": sum(1 for c in contacts if parse_ts(c.get("email1_sent_at", ""))),
        "completed": sum(1 for c in contacts if parse_ts(c.get("email3_sent_at", ""))),
    }
    smtp2 = connect_smtp()
    send_summary(smtp2, counts, total_progress, len(contacts), log_lines)
    smtp2.quit()

    sent_total = sum(v for k, v in counts.items() if k != "fail")
    print(f"\n{'─'*55}")
    print(f"  Sent this run  : {sent_total}")
    if counts["fail"]:
        print(f"  Failed         : {counts['fail']}")
    print(f"  Reached (E1)   : {total_progress['email1']}/{len(contacts)}")
    print(f"  Complete (E3)  : {total_progress['completed']}/{len(contacts)}")
    print(f"{'─'*55}\n")


if __name__ == "__main__":
    main()
