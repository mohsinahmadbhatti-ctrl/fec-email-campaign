import csv
import io
import os
from datetime import datetime, date

import requests
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, Response
)
from flask_sqlalchemy import SQLAlchemy

from models import db, Provider, Contact, ContactList, EmailTemplate, Campaign, CampaignSend
from email_providers import send_email

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'fec-email-portal-secret-2024')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///email_portal.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)


@app.template_filter('thousands')
def thousands_filter(value):
    try:
        return f'{int(value):,}'
    except (ValueError, TypeError):
        return value


def init_db():
    db.create_all()
    # Seed default providers if not present
    for name, limit in [('brevo', 300), ('mailjet', 200)]:
        if not Provider.query.filter_by(name=name).first():
            db.session.add(Provider(name=name, daily_limit=limit))
    db.session.commit()


# ─── Dashboard ────────────────────────────────────────────────────────────────

@app.route('/')
def dashboard():
    total_contacts = Contact.query.count()
    total_lists = ContactList.query.count()
    total_campaigns = Campaign.query.count()
    total_sent = CampaignSend.query.filter_by(status='sent').count()

    today = date.today()
    providers = Provider.query.all()
    for p in providers:
        if p.reset_date != today:
            p.sends_today = 0
            p.reset_date = today
    db.session.commit()

    today_sent = sum(p.sends_today for p in providers)
    today_capacity = sum(p.daily_limit for p in providers if p.enabled)
    recent_campaigns = Campaign.query.order_by(Campaign.created_at.desc()).limit(5).all()

    return render_template('dashboard.html',
        total_contacts=total_contacts,
        total_lists=total_lists,
        total_campaigns=total_campaigns,
        total_sent=total_sent,
        today_sent=today_sent,
        today_capacity=today_capacity,
        providers=providers,
        recent_campaigns=recent_campaigns,
    )


# ─── Contacts ─────────────────────────────────────────────────────────────────

@app.route('/contacts')
def contacts():
    page = request.args.get('page', 1, type=int)
    q = request.args.get('q', '').strip()
    query = Contact.query
    if q:
        like = f'%{q}%'
        query = query.filter(
            db.or_(Contact.email.ilike(like), Contact.first_name.ilike(like),
                   Contact.last_name.ilike(like), Contact.company.ilike(like))
        )
    pagination = query.order_by(Contact.created_at.desc()).paginate(page=page, per_page=50)
    return render_template('contacts/list.html', pagination=pagination, q=q)


@app.route('/contacts/import', methods=['GET', 'POST'])
def import_contacts():
    lists = ContactList.query.order_by(ContactList.name).all()
    if request.method == 'POST':
        file = request.files.get('csv_file')
        list_id = request.form.get('list_id')
        new_list_name = request.form.get('new_list_name', '').strip()

        if not file or not file.filename.endswith('.csv'):
            flash('Please upload a CSV file.', 'danger')
            return redirect(url_for('import_contacts'))

        contact_list = None
        if new_list_name:
            contact_list = ContactList(name=new_list_name)
            db.session.add(contact_list)
            db.session.flush()
        elif list_id:
            contact_list = ContactList.query.get(int(list_id))

        stream = io.StringIO(file.stream.read().decode('utf-8-sig', errors='replace'))
        reader = csv.DictReader(stream)
        headers = [h.strip().lower() for h in (reader.fieldnames or [])]

        col = lambda row, *names: next((row.get(n, '').strip() for n in names if n in headers), '')

        added = dupes = 0
        for row in reader:
            row = {k.strip().lower(): v.strip() for k, v in row.items()}
            email = col(row, 'email', 'email address', 'e-mail')
            if not email or '@' not in email:
                continue
            existing = Contact.query.filter_by(email=email.lower()).first()
            if existing:
                if contact_list and contact_list not in existing.lists:
                    existing.lists.append(contact_list)
                dupes += 1
                continue
            c = Contact(
                email=email.lower(),
                first_name=col(row, 'first_name', 'firstname', 'first name'),
                last_name=col(row, 'last_name', 'lastname', 'last name'),
                company=col(row, 'company', 'company_name', 'organization'),
                title=col(row, 'title', 'job_title', 'position'),
            )
            if contact_list:
                c.lists.append(contact_list)
            db.session.add(c)
            added += 1

        db.session.commit()
        flash(f'Imported {added} new contacts. {dupes} duplicates skipped.', 'success')
        return redirect(url_for('contacts'))

    return render_template('contacts/import.html', lists=lists)


@app.route('/contacts/<int:contact_id>/unsubscribe', methods=['POST'])
def unsubscribe_contact(contact_id):
    c = Contact.query.get_or_404(contact_id)
    c.unsubscribed = not c.unsubscribed
    db.session.commit()
    status = 'unsubscribed' if c.unsubscribed else 'resubscribed'
    flash(f'{c.email} has been {status}.', 'success')
    return redirect(request.referrer or url_for('contacts'))


@app.route('/contacts/import-legacy', methods=['POST'])
def import_legacy_csv():
    """Import existing contacts.csv from disk."""
    csv_path = os.path.join(os.path.dirname(__file__), 'contacts.csv')
    if not os.path.exists(csv_path):
        flash('contacts.csv not found.', 'danger')
        return redirect(url_for('contacts'))

    list_name = f"Legacy Import {datetime.utcnow().strftime('%Y-%m-%d')}"
    contact_list = ContactList(name=list_name, description='Imported from contacts.csv')
    db.session.add(contact_list)
    db.session.flush()

    added = dupes = 0
    with open(csv_path, encoding='utf-8-sig', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = row.get('email', '').strip().lower()
            if not email or '@' not in email:
                continue
            existing = Contact.query.filter_by(email=email).first()
            if existing:
                if contact_list not in existing.lists:
                    existing.lists.append(contact_list)
                dupes += 1
                continue
            c = Contact(
                email=email,
                first_name=row.get('first_name', '').strip(),
                last_name=row.get('last_name', '').strip(),
                company=row.get('company', '').strip(),
                title=row.get('title', '').strip(),
            )
            c.lists.append(contact_list)
            db.session.add(c)
            added += 1

    db.session.commit()
    flash(f'Imported {added} contacts from contacts.csv. {dupes} duplicates.', 'success')
    return redirect(url_for('contact_lists'))


# ─── Contact Lists ─────────────────────────────────────────────────────────────

@app.route('/lists')
def contact_lists():
    lists = ContactList.query.order_by(ContactList.created_at.desc()).all()
    return render_template('contacts/lists.html', lists=lists)


@app.route('/lists/create', methods=['GET', 'POST'])
def create_list():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        if not name:
            flash('List name is required.', 'danger')
            return redirect(url_for('create_list'))
        lst = ContactList(name=name, description=description)
        db.session.add(lst)
        db.session.commit()
        flash(f'List "{name}" created.', 'success')
        return redirect(url_for('contact_lists'))
    return render_template('contacts/create_list.html')


@app.route('/lists/<int:list_id>')
def list_detail(list_id):
    lst = ContactList.query.get_or_404(list_id)
    page = request.args.get('page', 1, type=int)
    contacts_q = Contact.query.filter(Contact.lists.contains(lst))
    pagination = contacts_q.paginate(page=page, per_page=50)
    return render_template('contacts/list_detail.html', lst=lst, pagination=pagination)


@app.route('/lists/<int:list_id>/delete', methods=['POST'])
def delete_list(list_id):
    lst = ContactList.query.get_or_404(list_id)
    db.session.delete(lst)
    db.session.commit()
    flash('List deleted.', 'success')
    return redirect(url_for('contact_lists'))


# ─── Email Templates ──────────────────────────────────────────────────────────

@app.route('/templates')
def templates():
    tmpl_list = EmailTemplate.query.order_by(EmailTemplate.created_at.desc()).all()
    return render_template('templates/list.html', templates=tmpl_list)


@app.route('/templates/create', methods=['GET', 'POST'])
def create_template():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        subject = request.form.get('subject', '').strip()
        body_html = request.form.get('body_html', '').strip()
        if not name or not subject or not body_html:
            flash('All fields are required.', 'danger')
            return render_template('templates/editor.html', template=None)
        t = EmailTemplate(name=name, subject=subject, body_html=body_html)
        db.session.add(t)
        db.session.commit()
        flash(f'Template "{name}" saved.', 'success')
        return redirect(url_for('templates'))
    return render_template('templates/editor.html', template=None)


@app.route('/templates/<int:tmpl_id>/edit', methods=['GET', 'POST'])
def edit_template(tmpl_id):
    t = EmailTemplate.query.get_or_404(tmpl_id)
    if request.method == 'POST':
        t.name = request.form.get('name', '').strip()
        t.subject = request.form.get('subject', '').strip()
        t.body_html = request.form.get('body_html', '').strip()
        t.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Template updated.', 'success')
        return redirect(url_for('templates'))
    return render_template('templates/editor.html', template=t)


@app.route('/templates/<int:tmpl_id>/delete', methods=['POST'])
def delete_template(tmpl_id):
    t = EmailTemplate.query.get_or_404(tmpl_id)
    db.session.delete(t)
    db.session.commit()
    flash('Template deleted.', 'success')
    return redirect(url_for('templates'))


@app.route('/templates/import-legacy', methods=['POST'])
def import_legacy_template():
    """Import existing email_template.txt into the DB."""
    tmpl_path = os.path.join(os.path.dirname(__file__), 'email_template.txt')
    if not os.path.exists(tmpl_path):
        flash('email_template.txt not found.', 'danger')
        return redirect(url_for('templates'))

    with open(tmpl_path, encoding='utf-8') as f:
        content = f.read()

    subject = ''
    body_lines = []
    in_body = False
    for line in content.splitlines():
        if line.startswith('Subject:') and not in_body:
            subject = line[8:].strip()
        elif line.strip() == '' and subject and not in_body:
            in_body = True
        elif in_body:
            body_lines.append(line)

    body_text = '\n'.join(body_lines).strip()
    body_html = '<br>'.join(body_text.replace('{', '{').splitlines())
    body_html = f'<p>{body_html}</p>'

    t = EmailTemplate(
        name='FEC Outreach (Legacy)',
        subject=subject or 'Hello from FEC',
        body_html=body_html,
    )
    db.session.add(t)
    db.session.commit()
    flash('Legacy template imported. Open it to edit the HTML.', 'success')
    return redirect(url_for('templates'))


# ─── Campaigns ────────────────────────────────────────────────────────────────

@app.route('/campaigns')
def campaigns():
    camp_list = Campaign.query.order_by(Campaign.created_at.desc()).all()
    return render_template('campaigns/list.html', campaigns=camp_list)


@app.route('/campaigns/create', methods=['GET', 'POST'])
def create_campaign():
    tmpl_list = EmailTemplate.query.order_by(EmailTemplate.name).all()
    lists = ContactList.query.order_by(ContactList.name).all()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        from_name = request.form.get('from_name', '').strip()
        from_email = request.form.get('from_email', '').strip()
        reply_to = request.form.get('reply_to', '').strip()
        template_id = request.form.get('template_id', type=int)
        list_id = request.form.get('list_id', type=int)
        daily_limit = request.form.get('daily_limit', 500, type=int)

        if not all([name, from_name, from_email, template_id, list_id]):
            flash('All required fields must be filled.', 'danger')
            return render_template('campaigns/create.html', templates=tmpl_list, lists=lists)

        c = Campaign(
            name=name,
            from_name=from_name,
            from_email=from_email,
            reply_to=reply_to,
            template_id=template_id,
            contact_list_id=list_id,
            daily_limit=daily_limit,
        )
        db.session.add(c)
        db.session.commit()
        flash(f'Campaign "{name}" created.', 'success')
        return redirect(url_for('campaign_detail', campaign_id=c.id))

    return render_template('campaigns/create.html', templates=tmpl_list, lists=lists)


@app.route('/campaigns/<int:campaign_id>')
def campaign_detail(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    sends = CampaignSend.query.filter_by(campaign_id=campaign_id)\
        .order_by(CampaignSend.sent_at.desc()).limit(100).all()
    return render_template('campaigns/detail.html', campaign=campaign, sends=sends)


@app.route('/campaigns/<int:campaign_id>/send-batch', methods=['POST'])
def send_batch(campaign_id):
    """Send one batch immediately (up to daily_limit_remaining contacts)."""
    campaign = Campaign.query.get_or_404(campaign_id)
    providers = Provider.query.filter_by(enabled=True).order_by(Provider.daily_limit.desc()).all()

    if not providers:
        flash('No email providers configured. Go to Settings first.', 'danger')
        return redirect(url_for('campaign_detail', campaign_id=campaign_id))

    total_remaining_today = sum(p.remaining_today() for p in providers)
    if total_remaining_today == 0:
        flash('Daily sending quota exhausted. Try again tomorrow.', 'warning')
        return redirect(url_for('campaign_detail', campaign_id=campaign_id))

    batch_size = min(int(request.form.get('batch_size', 50)), total_remaining_today)

    # Get contacts not yet sent to for this campaign
    sent_ids = db.session.query(CampaignSend.contact_id)\
        .filter_by(campaign_id=campaign_id)\
        .filter(CampaignSend.status.in_(['sent', 'failed']))\
        .subquery()

    pending = Contact.query\
        .filter(Contact.lists.contains(campaign.contact_list))\
        .filter(Contact.unsubscribed == False)\
        .filter(~Contact.id.in_(sent_ids))\
        .limit(batch_size).all()

    if not pending:
        campaign.status = 'completed'
        campaign.completed_at = datetime.utcnow()
        db.session.commit()
        flash('Campaign complete! All contacts have been sent to.', 'success')
        return redirect(url_for('campaign_detail', campaign_id=campaign_id))

    campaign.status = 'sending'
    if not campaign.started_at:
        campaign.started_at = datetime.utcnow()
    db.session.commit()

    sent_count = failed_count = 0
    for contact in pending:
        provider_name, error = send_email(providers, campaign, contact, campaign.template)
        send_record = CampaignSend(
            campaign_id=campaign_id,
            contact_id=contact.id,
            status='sent' if provider_name else 'failed',
            provider=provider_name,
            error_msg=error,
            sent_at=datetime.utcnow() if provider_name else None,
        )
        db.session.add(send_record)
        if provider_name:
            sent_count += 1
        else:
            failed_count += 1
            if 'No enabled provider' in error:
                break  # quota hit

    db.session.commit()
    flash(f'Batch sent: {sent_count} delivered, {failed_count} failed.', 'success' if sent_count else 'warning')
    return redirect(url_for('campaign_detail', campaign_id=campaign_id))


@app.route('/campaigns/<int:campaign_id>/pause', methods=['POST'])
def pause_campaign(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    c.status = 'paused' if c.status == 'sending' else 'sending'
    db.session.commit()
    flash(f'Campaign {c.status}.', 'info')
    return redirect(url_for('campaign_detail', campaign_id=campaign_id))


@app.route('/campaigns/<int:campaign_id>/delete', methods=['POST'])
def delete_campaign(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    CampaignSend.query.filter_by(campaign_id=campaign_id).delete()
    db.session.delete(c)
    db.session.commit()
    flash('Campaign deleted.', 'success')
    return redirect(url_for('campaigns'))


@app.route('/campaigns/<int:campaign_id>/export-sends')
def export_sends(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    sends = CampaignSend.query.filter_by(campaign_id=campaign_id).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['email', 'first_name', 'last_name', 'company', 'status', 'provider', 'sent_at', 'error'])
    for s in sends:
        c = s.contact
        writer.writerow([c.email, c.first_name, c.last_name, c.company,
                         s.status, s.provider, s.sent_at or '', s.error_msg])
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=campaign_{campaign_id}_sends.csv'}
    )


# ─── Settings ─────────────────────────────────────────────────────────────────

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    providers = {p.name: p for p in Provider.query.all()}
    if request.method == 'POST':
        for name in ['brevo', 'mailjet']:
            p = providers.get(name)
            if not p:
                p = Provider(name=name)
                db.session.add(p)
            p.api_key = request.form.get(f'{name}_api_key', '').strip()
            if name == 'mailjet':
                p.api_secret = request.form.get('mailjet_api_secret', '').strip()
            p.daily_limit = int(request.form.get(f'{name}_daily_limit', p.daily_limit))
            p.enabled = f'{name}_enabled' in request.form
        db.session.commit()
        flash('Settings saved.', 'success')
        return redirect(url_for('settings'))

    return render_template('settings.html', providers=providers)


@app.route('/settings/test-provider/<name>', methods=['POST'])
def test_provider(name):
    p = Provider.query.filter_by(name=name).first()
    if not p or not p.enabled or not p.api_key:
        return jsonify({'ok': False, 'msg': 'Provider not configured or disabled.'})
    try:
        if name == 'brevo':
            resp = requests.get(
                'https://api.brevo.com/v3/account',
                headers={'api-key': p.api_key},
                timeout=10,
            )
            if resp.status_code == 200:
                info = resp.json()
                return jsonify({'ok': True, 'msg': f"Connected as {info.get('email', 'unknown')}"})
            return jsonify({'ok': False, 'msg': f"HTTP {resp.status_code}: {resp.text[:200]}"})
        elif name == 'mailjet':
            resp = requests.get(
                'https://api.mailjet.com/v3/REST/apikey',
                auth=(p.api_key, p.api_secret),
                timeout=10,
            )
            if resp.status_code == 200:
                return jsonify({'ok': True, 'msg': 'Mailjet connected successfully.'})
            return jsonify({'ok': False, 'msg': f"HTTP {resp.status_code}: {resp.text[:200]}"})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})
    return jsonify({'ok': False, 'msg': 'Unknown provider.'})


if __name__ == '__main__':
    with app.app_context():
        init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
