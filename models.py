from datetime import datetime, date
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Provider(db.Model):
    __tablename__ = 'providers'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    daily_limit = db.Column(db.Integer, default=0)
    api_key = db.Column(db.String(500), default='')
    api_secret = db.Column(db.String(500), default='')
    enabled = db.Column(db.Boolean, default=False)
    sends_today = db.Column(db.Integer, default=0)
    reset_date = db.Column(db.Date, default=date.today)

    def remaining_today(self):
        if self.reset_date != date.today():
            self.sends_today = 0
            self.reset_date = date.today()
            db.session.commit()
        return max(0, self.daily_limit - self.sends_today)

    def increment(self, count=1):
        if self.reset_date != date.today():
            self.sends_today = 0
            self.reset_date = date.today()
        self.sends_today += count
        db.session.commit()


contact_list_membership = db.Table(
    'contact_list_membership',
    db.Column('contact_id', db.Integer, db.ForeignKey('contacts.id'), primary_key=True),
    db.Column('list_id', db.Integer, db.ForeignKey('contact_lists.id'), primary_key=True),
)


class ContactList(db.Model):
    __tablename__ = 'contact_lists'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    contacts = db.relationship('Contact', secondary=contact_list_membership, back_populates='lists')

    def count(self):
        return len(self.contacts)


class Contact(db.Model):
    __tablename__ = 'contacts'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(320), nullable=False, index=True)
    first_name = db.Column(db.String(100), default='')
    last_name = db.Column(db.String(100), default='')
    company = db.Column(db.String(200), default='')
    title = db.Column(db.String(200), default='')
    unsubscribed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    lists = db.relationship('ContactList', secondary=contact_list_membership, back_populates='contacts')
    sends = db.relationship('CampaignSend', back_populates='contact')

    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email


class EmailTemplate(db.Model):
    __tablename__ = 'email_templates'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    subject = db.Column(db.String(500), nullable=False)
    body_html = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    campaigns = db.relationship('Campaign', back_populates='template')


class Campaign(db.Model):
    __tablename__ = 'campaigns'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    from_name = db.Column(db.String(200), nullable=False)
    from_email = db.Column(db.String(320), nullable=False)
    reply_to = db.Column(db.String(320), default='')
    template_id = db.Column(db.Integer, db.ForeignKey('email_templates.id'), nullable=False)
    contact_list_id = db.Column(db.Integer, db.ForeignKey('contact_lists.id'), nullable=False)
    status = db.Column(db.String(30), default='draft')  # draft, sending, paused, completed
    daily_limit = db.Column(db.Integer, default=500)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    template = db.relationship('EmailTemplate', back_populates='campaigns')
    contact_list = db.relationship('ContactList')
    sends = db.relationship('CampaignSend', back_populates='campaign')

    def sent_count(self):
        return sum(1 for s in self.sends if s.status == 'sent')

    def failed_count(self):
        return sum(1 for s in self.sends if s.status == 'failed')

    def pending_count(self):
        total = len(self.contact_list.contacts) if self.contact_list else 0
        return total - self.sent_count() - self.failed_count()

    def progress_pct(self):
        total = len(self.contact_list.contacts) if self.contact_list else 0
        if total == 0:
            return 0
        return round((self.sent_count() / total) * 100)


class CampaignSend(db.Model):
    __tablename__ = 'campaign_sends'
    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id'), nullable=False)
    contact_id = db.Column(db.Integer, db.ForeignKey('contacts.id'), nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, sent, failed, skipped
    provider = db.Column(db.String(50), default='')
    error_msg = db.Column(db.Text, default='')
    sent_at = db.Column(db.DateTime, nullable=True)
    campaign = db.relationship('Campaign', back_populates='sends')
    contact = db.relationship('Contact', back_populates='sends')
