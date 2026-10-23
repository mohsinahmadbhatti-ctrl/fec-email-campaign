#!/usr/bin/env python3
"""
Email Sender — dual-campaign drip sequences.

Campaign 1 (OLD): Talent Assessments — 3 emails
Campaign 2 (MTO): MTO Hiring — 3 emails

Each contact goes through OLD first (if started), then MTO.
New contacts skip OLD and go straight to MTO.

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

OLD_TEMPLATE_FILES = {
    1: DIR / "email_template_1.txt",
    2: DIR / "email_template_2.txt",
    3: DIR / "email_template_3.txt",
}

MTO_TEMPLATE_FILES = {
    1: DIR / "mto_email_template_1.txt",
    2: DIR / "mto_email_template_2.txt",
    3: DIR / "mto_email_template_3.txt",
}

DELAY_DAYS = {2: 3, 3: 5}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_template_files(file_map: dict) -> dict[int, tuple[str, str]]:
    templates = {}
    for num, path in file_map.items():
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


def load_templates() -> tuple[dict, dict]:
    old = _parse_template_files(OLD_TEMPLATE_FILES)
    mto = _parse_template_files(MTO_TEMPLATE_FILES)
    return old, mto


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
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _seq_next(contact, prefix: str) -> int | None:
    """Check if email 1/2/3 is due for a given sequence prefix ('' or 'mto_')."""
    now = datetime.now(timezone.utc)
    e1 = parse_ts(contact.get(f"{prefix}email1_sent_at", ""))
    e2 = parse_ts(contact.get(f"{prefix}email2_sent_at", ""))
    e3 = parse_ts(contact.get(f"{prefix}email3_sent_at", ""))

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


def get_next_action(contact) -> tuple[str, int] | None:
    """
    Return (campaign, email_num) or None.

    Priority:
      1. Finish OLD sequence if started but incomplete
      2. MTO sequence (all contacts, after OLD is done or never started)
    """
    old_e1 = parse_ts(contact.get("email1_sent_at", ""))
    old_e3 = parse_ts(contact.get("email3_sent_at", ""))

    if old_e1 and not old_e3:
        nxt = _seq_next(contact, "")
        if nxt:
            return ("OLD", nxt)
        return None

    mto_nxt = _seq_next(contact, "mto_")
    if mto_nxt:
        return ("MTO", mto_nxt)
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
    old_e3 = counts.get("OLD_3", 0)
    mto_e1 = counts.get("MTO_1", 0)
    mto_e2 = counts.get("MTO_2", 0)
    mto_e3 = counts.get("MTO_3", 0)
    fails = counts.get("fail", 0)
    sent_total = old_e3 + mto_e1 + mto_e2 + mto_e3

    subject = f"Campaign update: {sent_total} sent — MTO {total_progress['mto_e1']}/{total_contacts}"

    body = f"""Campaign batch — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

Batch summary
─────────────────────────────
OLD  Email 3 (breakup) : {old_e3}
MTO  Email 1 (intro)   : {mto_e1}
MTO  Email 2 (follow)  : {mto_e2}
MTO  Email 3 (breakup) : {mto_e3}
Failed                 : {fails}
─────────────────────────────
OLD sequence done      : {total_progress['old_done']}/{total_contacts}
MTO reached (E1)       : {total_progress['mto_e1']}/{total_contacts}
MTO complete (E3)      : {total_progress['mto_done']}/{total_contacts}

Emails sent this batch:
"""
    for line in log_lines:
        body += f"  {line}\n"

    if fails:
        body += f"\n⚠️  {fails} email(s) failed — check GitHub Actions logs."

    if total_progress["mto_done"] == total_contacts:
        body += "\n\n🎉 Both campaigns complete — all contacts finished MTO sequence."

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

    old_templates, mto_templates = load_templates()
    contacts = load_contacts()

    due = []
    for contact in contacts:
        to_email = (contact.get("email") or "").strip()
        if not to_email:
            continue
        action = get_next_action(contact)
        if action:
            campaign, email_num = action
            due.append((contact, campaign, email_num))

    # Priority: OLD E3 first, then MTO follow-ups (E2/E3), then MTO E1
    def sort_key(item):
        _, camp, num = item
        if camp == "OLD":
            return (0, num)
        return (1 if num > 1 else 2, num)

    due.sort(key=sort_key)
    batch = due[: args.batch]

    old_pending = sum(1 for _, c, _ in due if c == "OLD")
    mto_pending = sum(1 for _, c, _ in due if c == "MTO")
    mto_e1_due  = sum(1 for _, c, n in due if c == "MTO" and n == 1)
    mto_e2_due  = sum(1 for _, c, n in due if c == "MTO" and n == 2)
    mto_e3_due  = sum(1 for _, c, n in due if c == "MTO" and n == 3)

    print(f"\n{'─'*55}")
    print(f"  Total contacts      : {len(contacts)}")
    print(f"  OLD pending (E3)    : {old_pending}")
    print(f"  MTO due E1/E2/E3    : {mto_e1_due}/{mto_e2_due}/{mto_e3_due}")
    print(f"  This batch          : {len(batch)}")
    print(f"  Dry run             : {args.dry_run}")
    print(f"{'─'*55}\n")

    if not batch:
        print("✅  No emails due right now.")
        return

    if args.dry_run:
        for contact, campaign, email_num in batch:
            to_email = (contact.get("email") or "").strip()
            name = f"{contact.get('first_name','')} {contact.get('last_name','')}".strip()
            company = contact.get("company", "") or "unknown"
            print(f"  DRY  [{campaign}] E{email_num}  {name:<28} → {to_email}  ({company})")
        return

    smtp = connect_smtp()

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
        for contact, campaign, email_num in batch:
            to_email = (contact.get("email") or "").strip()
            name     = f"{contact.get('first_name','')} {contact.get('last_name','')}".strip()
            company  = contact.get("company", "") or "unknown"

            if to_email in sent_emails_this_run:
                continue

            templates = old_templates if campaign == "OLD" else mto_templates
            subj_tpl, body_tpl = templates[email_num]
            subject = render(subj_tpl, contact)
            body    = render(body_tpl, contact)

            # Pick the right msg_id for threading
            if campaign == "OLD":
                prefix = ""
            else:
                prefix = "mto_"
            in_reply_to = contact.get(f"{prefix}msg_id", "").strip() or None
            msg = build_message(to_email, subject, body,
                                in_reply_to if email_num > 1 else None)

            try:
                smtp.sendmail(FROM_EMAIL, to_email, msg.as_string())
                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                contact[f"{prefix}email{email_num}_sent_at"] = now_str
                if email_num == 1:
                    contact[f"{prefix}msg_id"] = msg["Message-ID"]
                sent_emails_this_run.add(to_email)
                key = f"{campaign}_{email_num}"
                counts[key] = counts.get(key, 0) + 1
                log_lines.append(f"[{campaign}] E{email_num}  {name} <{to_email}> ({company})")
                print(f"  SENT [{campaign}] E{email_num}  {name:<28} → {to_email}")
            except Exception as e:
                counts["fail"] += 1
                print(f"  FAIL [{campaign}] E{email_num}  {name:<28} → {to_email}  ({e})")
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

    total_progress = {
        "old_done": sum(1 for c in contacts if parse_ts(c.get("email3_sent_at", ""))),
        "mto_e1":   sum(1 for c in contacts if parse_ts(c.get("mto_email1_sent_at", ""))),
        "mto_done": sum(1 for c in contacts if parse_ts(c.get("mto_email3_sent_at", ""))),
    }
    smtp2 = connect_smtp()
    send_summary(smtp2, counts, total_progress, len(contacts), log_lines)
    smtp2.quit()

    sent_total = sum(v for k, v in counts.items() if k != "fail")
    print(f"\n{'─'*55}")
    print(f"  Sent this run     : {sent_total}")
    if counts["fail"]:
        print(f"  Failed            : {counts['fail']}")
    print(f"  OLD done          : {total_progress['old_done']}/{len(contacts)}")
    print(f"  MTO reached (E1)  : {total_progress['mto_e1']}/{len(contacts)}")
    print(f"  MTO complete (E3) : {total_progress['mto_done']}/{len(contacts)}")
    print(f"{'─'*55}\n")


if __name__ == "__main__":
    main()
