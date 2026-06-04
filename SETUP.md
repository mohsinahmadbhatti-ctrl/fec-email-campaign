# FEC Email Marketing Portal — Free Setup Guide

## What this is
A self-hosted email marketing portal (like Odoo) that sends up to **500 emails/day for free** using:
- **Brevo** (formerly Sendinblue) — 300 emails/day, free forever, no credit card
- **Mailjet** — 200 emails/day, free forever, no credit card

**Total: 500 emails/day → your 3,247 contacts done in ~7 days.**

---

## Step 1: Get Free API Keys

### Brevo (300/day free)
1. Go to [brevo.com](https://www.brevo.com) → Create free account
2. Dashboard → Top-right menu → **SMTP & API** → **API Keys**
3. Click **Generate a New API Key**
4. Copy the key (starts with `xkeysib-...`)

### Mailjet (200/day free)
1. Go to [mailjet.com](https://www.mailjet.com) → Create free account
2. Dashboard → **Account Settings** → **REST API** → **API Key Management**
3. Copy both the **API Key** (public) and **Secret Key** (private)

---

## Step 2: Run the Portal

### Local (development)
```bash
pip install -r requirements.txt
python app.py
# Open: http://localhost:5000
```

### On a server (production)
```bash
pip install gunicorn
gunicorn -w 2 -b 0.0.0.0:5000 app:app
```

---

## Step 3: Initial Setup (5 minutes)

1. **Settings** → Paste your Brevo API key, Mailjet API key + secret → Enable both → Save
2. **Settings** → Click "Test Connection" on each to verify
3. **Contacts** → Click "Import Legacy contacts.csv" to import your 3,247 existing contacts
4. **Templates** → Click "Import Legacy Template" to import your existing email template, then edit the HTML
5. **Campaigns** → Create New Campaign → Select template + contact list → Create
6. **Campaign** page → Click "Send Batch" (e.g. 50 at a time) → Emails go out!

---

## Daily Workflow
- Open the portal → Go to your campaign → Click **Send Batch**
- Run batches throughout the day (up to 500 total)
- The portal tracks which contacts have been sent to — no duplicates

---

## Provider Limits (Free Tier)
| Provider | Daily Limit | Monthly | Credit Card? |
|----------|-------------|---------|-------------|
| Brevo    | 300         | 9,000   | No          |
| Mailjet  | 200         | 6,000   | No          |
| **Total** | **500**   | **15,000** | **No**  |

---

## Features
- Contact management with search & pagination
- Contact lists (segments)
- CSV import (supports your existing contacts.csv format)
- HTML email template editor with live preview
- Campaign creation and management
- Batch sending with provider auto-routing (uses Brevo first, then Mailjet)
- Per-campaign send log with status tracking
- Export send logs to CSV
- Unsubscribe management
- Settings with API key management and connection testing
