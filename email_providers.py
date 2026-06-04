import smtplib
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid
from datetime import datetime

import requests


def render_template(template_str, contact):
    """Replace {placeholders} in subject or body with contact fields."""
    replacements = {
        '{first_name}': contact.first_name or '',
        '{last_name}': contact.last_name or '',
        '{company_name}': contact.company or '',
        '{company}': contact.company or '',
        '{title}': contact.title or '',
        '{full_name}': contact.full_name(),
        '{email}': contact.email or '',
    }
    result = template_str
    for key, val in replacements.items():
        result = result.replace(key, val)
    return result


def send_via_brevo(api_key, from_email, from_name, to_email, to_name, subject, html_body, reply_to=None):
    """Send one email through Brevo (Sendinblue) REST API."""
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": api_key,
    }
    payload = {
        "sender": {"name": from_name, "email": from_email},
        "to": [{"email": to_email, "name": to_name}],
        "subject": subject,
        "htmlContent": html_body,
    }
    if reply_to:
        payload["replyTo"] = {"email": reply_to}

    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Brevo error {resp.status_code}: {resp.text}")
    return True


def send_via_mailjet(api_key, api_secret, from_email, from_name, to_email, to_name, subject, html_body, reply_to=None):
    """Send one email through Mailjet REST API."""
    url = "https://api.mailjet.com/v3.1/send"
    message = {
        "From": {"Email": from_email, "Name": from_name},
        "To": [{"Email": to_email, "Name": to_name}],
        "Subject": subject,
        "HTMLPart": html_body,
    }
    if reply_to:
        message["ReplyTo"] = {"Email": reply_to}

    payload = {"Messages": [message]}
    resp = requests.post(url, json=payload, auth=(api_key, api_secret), timeout=30)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Mailjet error {resp.status_code}: {resp.text}")
    data = resp.json()
    msg_status = data.get('Messages', [{}])[0].get('Status', '')
    if msg_status not in ('success', ''):
        raise RuntimeError(f"Mailjet rejected: {data}")
    return True


def send_via_smtp(host, port, username, password, from_email, from_name, to_email, to_name, subject, html_body, reply_to=None):
    """Send via generic SMTP (e.g. your own mail server)."""
    msg = MIMEMultipart('alternative')
    msg['From'] = formataddr((from_name, from_email))
    msg['To'] = formataddr((to_name, to_email))
    msg['Subject'] = subject
    msg['Message-ID'] = make_msgid()
    msg['Date'] = datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S +0000')
    if reply_to:
        msg['Reply-To'] = reply_to
    msg.attach(MIMEText(html_body, 'html'))

    with smtplib.SMTP(host, port, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.login(username, password)
        server.sendmail(from_email, [to_email], msg.as_string())
    return True


def send_email(providers, campaign, contact, template):
    """
    Send one email using whichever provider has remaining daily quota.
    providers: list of Provider model objects
    Returns (provider_name, error_message)
    """
    subject = render_template(template.subject, contact)
    body = render_template(template.body_html, contact)
    to_name = contact.full_name()

    for p in providers:
        if not p.enabled:
            continue
        remaining = p.remaining_today()
        if remaining <= 0:
            continue

        try:
            if p.name == 'brevo':
                send_via_brevo(
                    p.api_key,
                    campaign.from_email,
                    campaign.from_name,
                    contact.email,
                    to_name,
                    subject,
                    body,
                    campaign.reply_to or None,
                )
            elif p.name == 'mailjet':
                send_via_mailjet(
                    p.api_key,
                    p.api_secret,
                    campaign.from_email,
                    campaign.from_name,
                    contact.email,
                    to_name,
                    subject,
                    body,
                    campaign.reply_to or None,
                )
            elif p.name == 'smtp':
                parts = p.api_key.split(':')  # host:port:user:password
                host, port_str, user, pwd = parts[0], parts[1], parts[2], ':'.join(parts[3:])
                send_via_smtp(
                    host, int(port_str), user, pwd,
                    campaign.from_email, campaign.from_name,
                    contact.email, to_name,
                    subject, body,
                    campaign.reply_to or None,
                )
            p.increment(1)
            return p.name, ''
        except Exception as e:
            return '', str(e)

    return '', 'No enabled provider with remaining quota'
