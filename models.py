from flask_login import UserMixin
from datetime import datetime, UTC
from extensions import db

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(30), nullable=True)
    address = db.Column(db.String(200), nullable=True)
    birthday = db.Column(db.Date, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.Text)
    is_admin = db.Column(db.Boolean, default=False)
    is_moderator = db.Column(db.Boolean, default=False)
    can_create_events = db.Column(db.Boolean, default=False)
    can_add_event_status = db.Column(db.Boolean, default=False)
    can_adjust_character_status = db.Column(db.Boolean, default=False)
    can_accept_cast = db.Column(db.Boolean, default=False)
    can_arbitrate = db.Column(db.Boolean, default=False)
    membership_level = db.Column(db.String(20), default='None')  # None, Basic, Standard, Premium
    membership_expiry = db.Column(db.DateTime, nullable=True)
    date_registered = db.Column(db.DateTime, default=datetime.now(UTC))
    characters = db.relationship('Character', backref='user', lazy=True)
    
    def get_character_limit(self):
        """Get the character limit based on membership level"""
        if self.membership_level == 'Premium':
            return 50
        elif self.membership_level == 'Standard':
            return 25
        elif self.membership_level == 'Basic':
            return 10
        else:
            return 1
    
    def can_edit_characters(self):
        """Check if user can edit characters based on membership"""
        return self.membership_level != 'None'
    
    def get_editable_characters(self):
        """Get characters that can be edited based on membership limits"""
        if self.membership_level == 'None':
            return []  # No characters can be edited
        
        # Check if membership is expired
        if self.membership_expiry:
            # Ensure both datetimes are timezone-aware for comparison
            now = datetime.now(UTC)
            expiry = self.membership_expiry
            if expiry.tzinfo is None:
                # If expiry is timezone-naive, assume it's UTC
                expiry = expiry.replace(tzinfo=UTC)
            if expiry < now:
                return []  # No characters can be edited if membership is expired
        
        character_limit = self.get_character_limit()
        # Get characters ordered by creation date (oldest first)
        all_characters = Character.query.filter_by(user_id=self.id).order_by(Character.id.asc()).all()
        
        # For non-None memberships, return only the oldest characters up to the limit
        return all_characters[:character_limit]
    
    def is_membership_expired(self):
        """Check if membership is expired"""
        if self.membership_level == 'None':
            return True
        if self.membership_expiry:
            # Ensure both datetimes are timezone-aware for comparison
            now = datetime.now(UTC)
            expiry = self.membership_expiry
            if expiry.tzinfo is None:
                # If expiry is timezone-naive, assume it's UTC
                expiry = expiry.replace(tzinfo=UTC)
            return expiry < now
        return False

class Character(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    realm = db.Column(db.String(20), nullable=False)
    species = db.Column(db.String(20), nullable=False)
    group_name = db.Column(db.String(100), nullable=True)
    health = db.Column(db.Integer, default=0)
    stamina = db.Column(db.Integer, default=0)
    total_status = db.Column(db.Integer, default=5000)
    status_spent = db.Column(db.Integer, default=0)
    status_remaining = db.Column(db.Integer, default=5000)
    rank = db.Column(db.Integer, default=1)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    skills = db.relationship('CharacterSkill', backref='character', lazy=True)
    def __init__(self, *args, **kwargs):
        super(Character, self).__init__(*args, **kwargs)
        self.status_remaining = self.total_status
        if self.status_spent is None:
            self.status_spent = 0
        self.update_rank()
    def update_rank(self):
        status_spent = self.status_spent or 0
        if status_spent <= 5000:
            self.rank = 1
        elif status_spent <= 10000:
            self.rank = 2
        elif status_spent <= 15000:
            self.rank = 3
        elif status_spent <= 20000:
            self.rank = 4
        else:
            self.rank = 5

class CharacterSkill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    character_id = db.Column(db.Integer, db.ForeignKey('character.id'), nullable=False)
    skill_id = db.Column(db.Integer, db.ForeignKey('skill.id'), nullable=False)
    skill = db.relationship('Skill')

class StatusAdjustment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    character_id = db.Column(db.Integer, db.ForeignKey('character.id'), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    status_type = db.Column(db.String(20), nullable=False)
    notes = db.Column(db.Text)
    date = db.Column(db.DateTime, default=datetime.now(UTC))
    adjusted_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=True)
    character = db.relationship('Character', backref='status_adjustments')
    user = db.relationship('User', backref='status_adjustments_made')
    event = db.relationship('Event', backref='status_adjustments')

class StatusPurchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    character_id = db.Column(db.Integer, db.ForeignKey('character.id'), nullable=False)
    amount = db.Column(db.Integer, default=100)
    price = db.Column(db.Float, default=10.00)
    date = db.Column(db.DateTime, default=datetime.now(UTC))
    status = db.Column(db.String(20), default='Pending')
    character = db.relationship('Character', backref='status_purchases')

class Skill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lore_category = db.Column(db.String(50), nullable=False)
    sub_category = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    cost = db.Column(db.Integer, nullable=False)
    rank = db.Column(db.Integer, nullable=True)
    resources = db.Column(db.Integer, default=0)

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    realm = db.Column(db.String(20), nullable=False)
    start_date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime, nullable=False)
    location = db.Column(db.String(100), nullable=False)
    timeblocks = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='Upcoming')
    processed = db.Column(db.Boolean, default=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    participants = db.relationship('EventParticipation', backref='event', lazy=True)

class EventParticipation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    character_id = db.Column(db.Integer, db.ForeignKey('character.id'), nullable=False)
    timeblock = db.Column(db.Integer, nullable=False)
    service_performed = db.Column(db.Boolean, default=False)
    decorated_area = db.Column(db.Boolean, default=False)
    resources_used = db.Column(db.Integer, default=0)
    treasure_turned_in = db.Column(db.Integer, default=0)
    status_gained = db.Column(db.Integer, default=0)
    completed = db.Column(db.Boolean, default=False)
    character = db.relationship('Character', backref='event_participations')
    user = db.relationship('User', backref='event_participations')

class CastSignup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    character_id = db.Column(db.Integer, db.ForeignKey('character.id'), nullable=False)
    timeblock = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='Pending')
    writing_status = db.Column(db.Integer, default=0)
    management_status = db.Column(db.Integer, default=0)
    notes = db.Column(db.Text)
    character = db.relationship('Character', backref='cast_signups')
    user = db.relationship('User', backref='cast_signups')
    event = db.relationship('Event', backref='cast_signups')

class Complaint(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    complainant_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    accused_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    offense = db.Column(db.String(100), nullable=False)
    penalty = db.Column(db.String(100), nullable=True)
    date_of_offense = db.Column(db.Date, nullable=False)
    description = db.Column(db.Text, nullable=False)
    date_filed = db.Column(db.DateTime, default=datetime.now(UTC))
    status = db.Column(db.String(20), default='Unresolved')
    arbitrator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    resolution = db.Column(db.String(20), nullable=True)
    resolution_reason = db.Column(db.Text, nullable=True)
    resolution_attempt = db.Column(db.Text, nullable=False)
    people_involved = db.Column(db.Text, nullable=True)
    complainant = db.relationship('User', foreign_keys=[complainant_id], backref='complaints_filed')
    accused = db.relationship('User', foreign_keys=[accused_id], backref='complaints_against')
    arbitrator = db.relationship('User', foreign_keys=[arbitrator_id], backref='complaints_arbitrated') 