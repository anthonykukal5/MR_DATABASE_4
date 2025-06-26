from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
from datetime import datetime, UTC, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import logging
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from io import BytesIO
from sqlalchemy import func
import pytz
from sqlalchemy import text

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Timezone configuration
EST = pytz.timezone('US/Eastern')

def convert_to_utc(est_datetime):
    """Convert EST datetime to UTC"""
    if est_datetime.tzinfo is None:
        est_datetime = EST.localize(est_datetime)
    return est_datetime.astimezone(UTC)

def convert_to_est(utc_datetime):
    """Convert UTC datetime to EST"""
    if utc_datetime.tzinfo is None:
        utc_datetime = UTC.localize(utc_datetime)
    return utc_datetime.astimezone(EST)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'  # Change this to a secure secret key
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///mystic_realms.db'

# Add timezone to template context
@app.context_processor
def inject_timezone():
    return {'EST': EST}

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Species by realm mapping
SPECIES_BY_REALM = {
    'Everstars': ['Human', 'Android', 'Gen-E'],
    'Guildhall': ['Human', 'Elf', 'Orc'],
    'Tyrs': ['Human', 'Ghoul', 'Airadin']
}

# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(30), nullable=True)
    address = db.Column(db.String(200), nullable=True)
    birthday = db.Column(db.Date, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    is_admin = db.Column(db.Boolean, default=False)
    is_moderator = db.Column(db.Boolean, default=False)
    can_create_events = db.Column(db.Boolean, default=False)
    can_add_event_status = db.Column(db.Boolean, default=False)
    can_adjust_character_status = db.Column(db.Boolean, default=False)
    can_accept_cast = db.Column(db.Boolean, default=False)  # New permission
    characters = db.relationship('Character', backref='user', lazy=True)

class Character(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    realm = db.Column(db.String(20), nullable=False)
    species = db.Column(db.String(20), nullable=False)
    group_name = db.Column(db.String(100), nullable=True)  # Changed from group to group_name
    health = db.Column(db.Integer, default=0)
    stamina = db.Column(db.Integer, default=0)
    total_status = db.Column(db.Integer, default=5000)  # Starting status
    status_spent = db.Column(db.Integer, default=0)
    status_remaining = db.Column(db.Integer, default=5000)  # Should match total_status initially
    rank = db.Column(db.Integer, default=1)  # New rank field
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    skills = db.relationship('CharacterSkill', backref='character', lazy=True)

    def __init__(self, *args, **kwargs):
        super(Character, self).__init__(*args, **kwargs)
        # Ensure status_remaining matches total_status initially
        self.status_remaining = self.total_status
        # Ensure status_spent is initialized
        if self.status_spent is None:
            self.status_spent = 0
        self.update_rank()

    def update_rank(self):
        """Update character rank based on status spent"""
        status_spent = self.status_spent or 0  # Handle None case
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

class Skill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lore_category = db.Column(db.String(50), nullable=False)
    sub_category = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    cost = db.Column(db.Integer, nullable=False)
    rank = db.Column(db.Integer, nullable=True)
    resources = db.Column(db.Integer, default=0)  # New: resources granted by this skill

class CharacterSkill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    character_id = db.Column(db.Integer, db.ForeignKey('character.id'), nullable=False)
    skill_id = db.Column(db.Integer, db.ForeignKey('skill.id'), nullable=False)
    skill = db.relationship('Skill')

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    realm = db.Column(db.String(20), nullable=False)
    start_date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime, nullable=False)
    location = db.Column(db.String(100), nullable=False)
    timeblocks = db.Column(db.Integer, nullable=False)  # Number of timeblocks in the event
    status = db.Column(db.String(20), default='Upcoming')  # Upcoming, In Progress, Completed
    processed = db.Column(db.Boolean, default=False)  # Track if event has been processed for status
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    participants = db.relationship('EventParticipation', backref='event', lazy=True)

class EventParticipation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    character_id = db.Column(db.Integer, db.ForeignKey('character.id'), nullable=False)
    timeblock = db.Column(db.Integer, nullable=False)  # Which timeblock this participation is for
    service_performed = db.Column(db.Boolean, default=False)
    decorated_area = db.Column(db.Boolean, default=False)
    resources_used = db.Column(db.Integer, default=0)
    treasure_turned_in = db.Column(db.Integer, default=0)
    status_gained = db.Column(db.Integer, default=0)
    completed = db.Column(db.Boolean, default=False)
    
    # Add relationships
    character = db.relationship('Character', backref='event_participations')
    user = db.relationship('User', backref='event_participations')

class StatusAdjustment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    character_id = db.Column(db.Integer, db.ForeignKey('character.id'), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    status_type = db.Column(db.String(20), nullable=False)  # Writing, Management, Live, Dice, Service
    notes = db.Column(db.Text)
    date = db.Column(db.DateTime, default=datetime.now(UTC))
    adjusted_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    character = db.relationship('Character', backref='status_adjustments')
    user = db.relationship('User', backref='status_adjustments_made')

class StatusPurchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    character_id = db.Column(db.Integer, db.ForeignKey('character.id'), nullable=False)
    amount = db.Column(db.Integer, default=100)  # Fixed at 100 status points
    price = db.Column(db.Float, default=10.00)   # Fixed at $10.00
    date = db.Column(db.DateTime, default=datetime.now(UTC))
    status = db.Column(db.String(20), default='Pending')  # Pending, Completed, Failed
    
    character = db.relationship('Character', backref='status_purchases')

class CastSignup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    character_id = db.Column(db.Integer, db.ForeignKey('character.id'), nullable=False)
    timeblock = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='Pending')  # Pending, Accepted, Denied
    writing_status = db.Column(db.Integer, default=0)
    management_status = db.Column(db.Integer, default=0)
    notes = db.Column(db.Text)
    
    # Add relationships
    character = db.relationship('Character', backref='cast_signups')
    user = db.relationship('User', backref='cast_signups')
    event = db.relationship('Event', backref='cast_signups')

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Routes
@app.route('/')
def home():
    return render_template('home.html')

@app.route('/books')
def books():
    return render_template('books.html')

@app.route('/events')
@login_required
def events():
    # Update event statuses immediately
    now = datetime.now()
    
    # Update upcoming to in progress
    Event.query.filter(
        Event.status == 'Upcoming',
        Event.start_date <= now
    ).update({'status': 'In Progress'})
    
    # Update in progress to completed
    Event.query.filter(
        Event.status == 'In Progress',
        Event.end_date <= now
    ).update({'status': 'Completed'})
    
    db.session.commit()
    
    # Get events in different states using the status field
    upcoming_events = Event.query.filter_by(status='Upcoming').all()
    in_progress_events = Event.query.filter_by(status='In Progress').all()
    completed_events = Event.query.filter_by(status='Completed').all()
    
    # Function to get unique participant and cast counts for an event
    def get_event_counts(event):
        # Get unique participant count
        participant_count = db.session.query(EventParticipation.user_id)\
            .filter_by(event_id=event.id)\
            .distinct()\
            .count()
        
        # Get unique cast count
        cast_count = db.session.query(CastSignup.user_id)\
            .filter_by(event_id=event.id)\
            .distinct()\
            .count()
        
        return {
            'participant_count': participant_count,
            'cast_count': cast_count
        }
    
    # Add counts to each event
    upcoming_events = [(event, get_event_counts(event)) for event in upcoming_events]
    in_progress_events = [(event, get_event_counts(event)) for event in in_progress_events]
    completed_events = [(event, get_event_counts(event)) for event in completed_events]
    
    return render_template('events.html',
                         upcoming_events=upcoming_events,
                         in_progress_events=in_progress_events,
                         completed_events=completed_events)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        address = request.form.get('address')
        birthday_str = request.form.get('birthday')
        password = request.form.get('password')
        from datetime import datetime
        birthday = datetime.strptime(birthday_str, '%Y-%m-%d').date() if birthday_str else None
        if not birthday:
            flash('Birthday is required')
            return redirect(url_for('register'))
        if User.query.filter_by(email=email).first():
            flash('Email already registered')
            return redirect(url_for('register'))
        user = User(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            address=address,
            birthday=birthday,
            password_hash=generate_password_hash(password)
        )
        db.session.add(user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('my_characters'))
        
        flash('Invalid email or password')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/my_characters')
@login_required
def my_characters():
    characters = Character.query.filter_by(user_id=current_user.id).all()
    return render_template('my_characters.html', characters=characters)

@app.route('/create_character', methods=['GET', 'POST'])
@login_required
def create_character():
    if request.method == 'POST':
        name = request.form.get('name')
        realm = request.form.get('realm')
        species = request.form.get('species')
        
        character = Character(
            name=name,
            realm=realm,
            species=species,
            user_id=current_user.id
        )
        db.session.add(character)
        db.session.commit()
        
        return redirect(url_for('edit_character', character_id=character.id))
    
    return render_template('create_character.html')

@app.route('/edit_character/<int:character_id>', methods=['GET', 'POST'])
@login_required
def edit_character(character_id):
    character = Character.query.get_or_404(character_id)
    if character.user_id != current_user.id:
        return redirect(url_for('my_characters'))
    
    if request.method == 'POST':
        try:
            # Update basic character info
            character.name = request.form.get('name')
            character.species = request.form.get('species')
            character.group_name = request.form.get('group')  # Updated to use group_name
            character.health = int(request.form.get('health', 0))
            character.stamina = int(request.form.get('stamina', 0))
            
            # Calculate status costs
            total_spent = 0
            
            # Health cost (200 per point)
            total_spent += character.health * 200
            
            # Stamina cost (progressive)
            stamina = character.stamina
            if stamina > 0:
                if stamina <= 5:
                    total_spent += stamina * 100
                elif stamina <= 10:
                    total_spent += 500 + (stamina - 5) * 200
                elif stamina <= 15:
                    total_spent += 1500 + (stamina - 10) * 300
                elif stamina <= 20:
                    total_spent += 3000 + (stamina - 15) * 400
                else:
                    total_spent += 5000 + (stamina - 20) * 500
            
            # Update skills
            selected_skills = request.form.getlist('skills')
            
            # Remove all existing skills
            CharacterSkill.query.filter_by(character_id=character.id).delete()
            
            # Add selected skills
            for skill_id in selected_skills:
                skill = Skill.query.get(skill_id)
                if skill:
                    character_skill = CharacterSkill(
                        character_id=character.id,
                        skill_id=skill.id
                    )
                    db.session.add(character_skill)
                    total_spent += skill.cost
            
            # Update status information
            character.status_spent = total_spent
            character.status_remaining = character.total_status - total_spent
            
            # Update rank based on status spent
            character.update_rank()
            
            # Validate total status spent
            if total_spent > character.total_status:
                flash('Error: Total status spent exceeds available status points')
                return redirect(url_for('edit_character', character_id=character.id))
            
            db.session.commit()
            flash('Character updated successfully!')
            return redirect(url_for('my_characters'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating character: {str(e)}')
            return redirect(url_for('edit_character', character_id=character.id))
    
    # Get all skills and organize them by category and subcategory
    skills = Skill.query.all()
    skills_by_category = {}
    skills_by_subcategory = {}
    
    for skill in skills:
        if skill.lore_category not in skills_by_category:
            skills_by_category[skill.lore_category] = []
            skills_by_subcategory[skill.lore_category] = {}
        
        if skill.sub_category not in skills_by_subcategory[skill.lore_category]:
            skills_by_subcategory[skill.lore_category][skill.sub_category] = []
        
        skills_by_subcategory[skill.lore_category][skill.sub_category].append(skill)
    
    # Get character's current skills
    character_skills = [cs.skill_id for cs in character.skills]
    
    return render_template('edit_character.html',
                         character=character,
                         species_by_realm=SPECIES_BY_REALM,
                         skills_by_category=skills_by_category,
                         skills_by_subcategory=skills_by_subcategory,
                         character_skills=character_skills)

# Helper to calculate character resources
def get_character_resources(character):
    return sum(cs.skill.resources or 0 for cs in character.skills)

@app.route('/view_character/<int:character_id>')
@login_required
def view_character(character_id):
    character = Character.query.get_or_404(character_id)
    if character.user_id != current_user.id:
        return redirect(url_for('my_characters'))
    
    # Get all skills and organize them by category and subcategory
    skills = Skill.query.all()
    skills_by_category = {}
    skills_by_subcategory = {}
    
    for skill in skills:
        if skill.lore_category not in skills_by_category:
            skills_by_category[skill.lore_category] = []
            skills_by_subcategory[skill.lore_category] = {}
        
        if skill.sub_category not in skills_by_subcategory[skill.lore_category]:
            skills_by_subcategory[skill.lore_category][skill.sub_category] = []
        
        skills_by_subcategory[skill.lore_category][skill.sub_category].append(skill)
    
    # Get character's current skills
    character_skills = [cs.skill_id for cs in character.skills]
    resources = get_character_resources(character)
    
    return render_template('view_character.html',
                         character=character,
                         skills_by_category=skills_by_category,
                         skills_by_subcategory=skills_by_subcategory,
                         character_skills=character_skills,
                         resources=resources)

@app.route('/create_event', methods=['GET', 'POST'])
@login_required
def create_event():
    if request.method == 'POST':
        title = request.form.get('title')
        realm = request.form.get('realm')
        timeblocks = int(request.form.get('timeblocks'))
        
        # Get datetime strings from form
        start_date_str = request.form.get('start_date')
        end_date_str = request.form.get('end_date')
        
        # Parse the datetime strings (they are in local time)
        start_date = datetime.fromisoformat(start_date_str)
        end_date = datetime.fromisoformat(end_date_str)
        
        # Store the times as-is, without timezone conversion
        location = request.form.get('location')
        
        event = Event(
            title=title,
            realm=realm,
            timeblocks=timeblocks,
            start_date=start_date,
            end_date=end_date,
            location=location,
            created_by=current_user.id
        )
        
        db.session.add(event)
        db.session.commit()
        
        flash('Event created successfully!')
        return redirect(url_for('events'))
    
    return render_template('create_event.html')

@app.route('/signup_event/<int:event_id>', methods=['GET', 'POST'])
@login_required
def signup_event(event_id):
    event = Event.query.get_or_404(event_id)
    
    # Check if event has started using local time
    if event.start_date <= datetime.now():
        flash('Cannot sign up for an event that has already started')
        return redirect(url_for('events'))
    
    if request.method == 'POST':
        signup_type = request.form.get('signup_type')
        
        if signup_type == 'participant':
            # Process participant signup
            for timeblock in range(1, event.timeblocks + 1):
                character_id = request.form.get(f'character_{timeblock}')
                if character_id:  # Only process if a character was selected for this timeblock
                    # Check if already signed up as participant or cast for this timeblock
                    existing_participant = EventParticipation.query.filter_by(
                        event_id=event_id,
                        user_id=current_user.id,
                        timeblock=timeblock
                    ).first()
                    
                    existing_cast = CastSignup.query.filter_by(
                        event_id=event_id,
                        user_id=current_user.id,
                        timeblock=timeblock
                    ).first()
                    
                    if existing_participant or existing_cast:
                        flash(f'You are already signed up for timeblock {timeblock}')
                        continue
                    
                    participation = EventParticipation(
                        event_id=event_id,
                        user_id=current_user.id,
                        character_id=character_id,
                        timeblock=timeblock
                    )
                    db.session.add(participation)
        
        elif signup_type == 'cast':
            # Process cast signup
            character_id = request.form.get('cast_character')
            selected_timeblocks = request.form.getlist('cast_timeblocks')
            
            for timeblock in selected_timeblocks:
                timeblock = int(timeblock)
                # Check if already signed up as participant or cast for this timeblock
                existing_participant = EventParticipation.query.filter_by(
                    event_id=event_id,
                    user_id=current_user.id,
                    timeblock=timeblock
                ).first()
                
                existing_cast = CastSignup.query.filter_by(
                    event_id=event_id,
                    user_id=current_user.id,
                    timeblock=timeblock
                ).first()
                
                if existing_participant or existing_cast:
                    flash(f'You are already signed up for timeblock {timeblock}')
                else:
                    cast_signup = CastSignup(
                        event_id=event_id,
                        user_id=current_user.id,
                        character_id=character_id,
                        timeblock=timeblock
                    )
                    db.session.add(cast_signup)
        
        db.session.commit()
        flash('Successfully signed up for the event!')
        return redirect(url_for('events'))
    
    # Get user's characters in the event's realm for participant signup
    realm_characters = Character.query.filter_by(
        user_id=current_user.id,
        realm=event.realm
    ).all()
    
    # Get all user's characters for cast signup
    all_characters = Character.query.filter_by(
        user_id=current_user.id
    ).all()
    
    return render_template('event_signup.html', 
                         event=event, 
                         realm_characters=realm_characters,
                         all_characters=all_characters)

@app.route('/status_management')
@login_required
def status_management():
    # Events with unprocessed participants (for add event status)
    unprocessed_events = db.session.query(Event).filter(
        Event.status == 'Completed',
        db.session.query(EventParticipation).filter(
            EventParticipation.event_id == Event.id
        ).outerjoin(
            StatusAdjustment,
            (StatusAdjustment.character_id == EventParticipation.character_id) &
            (StatusAdjustment.notes.like('Event: ' + Event.title + '%'))
        ).filter(
            StatusAdjustment.id == None
        ).exists()
    ).order_by(Event.start_date.desc()).all()

    # Events with pending cast signups (for manage cast)
    cast_events = db.session.query(Event).join(
        CastSignup,
        Event.id == CastSignup.event_id
    ).filter(
        Event.status == 'Completed',
        CastSignup.status == 'Pending'
    ).group_by(Event.id).order_by(Event.start_date.desc()).all()

    return render_template('status_management.html',
                         unprocessed_events=unprocessed_events,
                         cast_events=cast_events)

@app.route('/get_event_participants/<int:event_id>')
@login_required
def get_event_participants(event_id):
    event = Event.query.get_or_404(event_id)
    
    if event.processed:
        return redirect(url_for('status_management'))
    
    # Get all participants and their timeblock counts who haven't had status adjustments yet
    participants = db.session.query(
        Character,
        func.count(EventParticipation.timeblock).label('timeblock_count')
    ).join(
        EventParticipation,
        Character.id == EventParticipation.character_id
    ).outerjoin(
        StatusAdjustment,
        (StatusAdjustment.character_id == Character.id) & 
        (StatusAdjustment.notes.like(f'Event: {event.title}%'))
    ).filter(
        EventParticipation.event_id == event_id,
        StatusAdjustment.id == None  # Only get characters without status adjustments for this event
    ).group_by(Character.id).order_by(Character.id).all()
    
    return render_template('event_participants.html',
                         event=event,
                         participants=participants)

@app.route('/adjust_event_status', methods=['POST'])
@login_required
def adjust_event_status():
    character_id = request.form.get('character_id')
    event_id = request.form.get('event_id')
    writing_status = int(request.form.get('writing_status', 0))
    management_status = int(request.form.get('management_status', 0))
    service_status = int(request.form.get('service_status', 0))
    cast_status = int(request.form.get('cast_status', 0))
    interaction_status = int(request.form.get('interaction_status', 0))
    
    character = Character.query.get_or_404(character_id)
    event = Event.query.get_or_404(event_id)
    
    if event.processed:
        flash('This event has already been processed')
        return redirect(url_for('status_management'))
    
    # Calculate play status (25 per timeblock)
    timeblock_count = EventParticipation.query.filter_by(
        event_id=event_id,
        character_id=character_id
    ).count()
    play_status = timeblock_count * 25
    
    # Calculate total status to be added
    total_status = (
        writing_status +
        management_status +
        service_status +
        cast_status +
        interaction_status +
        play_status
    )
    
    # Create status adjustment records for each type
    status_types = {
        'Writing': writing_status,
        'Management': management_status,
        'Service': service_status,
        'Cast': cast_status,
        'Interaction': interaction_status,
        'Play': play_status
    }
    
    for status_type, amount in status_types.items():
        if amount > 0:
            adjustment = StatusAdjustment(
                character_id=character.id,
                amount=amount,
                status_type=status_type,
                notes=f'Event: {event.title}',
                adjusted_by=current_user.id
            )
            db.session.add(adjustment)
    
    # Update character's total status
    character.total_status += total_status
    character.status_remaining += total_status
    character.update_rank()
    
    # Check if this was the last participant
    remaining_participants = EventParticipation.query.filter_by(
        event_id=event_id
    ).group_by(EventParticipation.character_id).count()
    
    # Check if there are any pending cast signups
    pending_cast_signups = CastSignup.query.filter_by(
        event_id=event_id,
        status='Pending'
    ).count()
    
    # Only mark as processed if this was the last participant and there are no pending cast signups
    if remaining_participants == 1 and pending_cast_signups == 0:
        event.processed = True
    
    db.session.commit()
    flash(f'Successfully added {total_status} status points to {character.name}')
    return redirect(url_for('get_event_participants', event_id=event_id))

def get_status_totals_by_type(character_id):
    """Calculate total status for each type for a character"""
    adjustments = StatusAdjustment.query.filter_by(character_id=character_id).all()
    event_participations = EventParticipation.query.filter_by(character_id=character_id).all()
    
    totals = {
        'Writing': 0,
        'Management': 0,
        'Service': 0,
        'Cast': 0,
        'Interaction': 0,
        'Play': 0
    }
    
    # Add status from adjustments
    for adj in adjustments:
        if adj.status_type in totals:  # Only add if it's a valid status type
            totals[adj.status_type] += adj.amount
    
    return totals

@app.route('/character/<int:character_id>/status_history')
@login_required
def character_status_history(character_id):
    character = Character.query.get_or_404(character_id)
    
    # Verify the character belongs to the current user
    if character.user_id != current_user.id:
        flash('You do not have permission to view this character\'s history')
        return redirect(url_for('my_characters'))
    
    # Get status totals by type
    status_totals = get_status_totals_by_type(character_id)
    
    # Get status adjustments
    adjustments = StatusAdjustment.query.filter_by(character_id=character_id).all()
    adjustment_history = [{
        'date': adj.date,
        'amount': adj.amount,
        'reason': adj.status_type,
        'notes': adj.notes,
        'source': 'Manual Adjustment'
    } for adj in adjustments]
    
    # Get event participations
    participations = EventParticipation.query.filter_by(character_id=character_id).all()
    event_history = [{
        'date': event.event.start_date,
        'amount': event.status_gained,
        'reason': 'Service',
        'notes': f'Event: {event.event.title} - Timeblock {event.timeblock}',
        'source': 'Event'
    } for event in participations if event.status_gained > 0]
    
    # Combine and sort all transactions by date
    status_history = adjustment_history + event_history
    status_history.sort(key=lambda x: x['date'])
    
    return render_template('character_status_history.html',
                         character=character,
                         status_history=status_history,
                         status_totals=status_totals)

@app.route('/adjust_character_status', methods=['POST'])
@login_required
def adjust_character_status():
    character_search = request.form.get('character_search', '').strip()
    status_amount = int(request.form.get('status_amount', 0))
    status_type = request.form.get('status_type')
    notes = request.form.get('notes')
    
    # If this is a search request
    if request.form.get('action') == 'search':
        if not character_search:
            flash('Please enter a character name or ID to search')
            return redirect(url_for('status_management'))
            
        # Search for characters matching the search term
        try:
            # Try to convert search term to integer for ID search
            character_id = int(character_search)
            characters = Character.query.filter_by(id=character_id).all()
        except ValueError:
            # If not an integer, search by name
            characters = Character.query.filter(
                Character.name.ilike(f'%{character_search}%')
            ).all()
            
        if not characters:
            flash('No characters found matching your search')
            
        return render_template('status_management.html',
                             search_results=characters,
                             user_characters=Character.query.filter_by(user_id=current_user.id).all(),
                             status_amount=status_amount,
                             status_type=status_type,
                             notes=notes,
                             character_search=character_search)
    
    # If adjusting status, get the specific character
    character_id = request.form.get('character_id')
    if not character_id:
        flash('Please select a character to adjust status')
        return redirect(url_for('status_management'))
    
    character = Character.query.get_or_404(character_id)
    
    # Create status adjustment record
    adjustment = StatusAdjustment(
        character_id=character.id,
        amount=status_amount,
        status_type=status_type,
        notes=notes,
        adjusted_by=current_user.id
    )
    
    # Update character status
    character.total_status += status_amount
    character.status_remaining += status_amount
    character.update_rank()  # Update rank after status change
    
    db.session.add(adjustment)
    db.session.commit()
    
    flash(f'Successfully adjusted status for {character.name}')
    return redirect(url_for('status_management'))

@app.route('/purchase_status', methods=['POST'])
@login_required
def purchase_status():
    character_id = request.form.get('character_id')
    character = Character.query.get_or_404(character_id)
    
    # Verify the character belongs to the current user
    if character.user_id != current_user.id:
        flash('Invalid character selection')
        return redirect(url_for('status_management'))
    
    # Create purchase record
    purchase = StatusPurchase(
        character_id=character.id,
        status='Pending'  # In a real implementation, this would be updated after payment processing
    )
    
    db.session.add(purchase)
    db.session.commit()
    
    flash('Status purchase request submitted. Payment processing will be implemented soon.')
    return redirect(url_for('status_management'))

@app.route('/delete_character/<int:character_id>', methods=['POST'])
@login_required
def delete_character(character_id):
    character = Character.query.get_or_404(character_id)
    
    # Verify the character belongs to the current user
    if character.user_id != current_user.id:
        flash('You do not have permission to delete this character')
        return redirect(url_for('my_characters'))
    
    try:
        # Delete all related records first
        CharacterSkill.query.filter_by(character_id=character.id).delete()
        StatusAdjustment.query.filter_by(character_id=character.id).delete()
        StatusPurchase.query.filter_by(character_id=character.id).delete()
        EventParticipation.query.filter_by(character_id=character.id).delete()
        
        # Delete the character
        db.session.delete(character)
        db.session.commit()
        
        flash(f'Character {character.name} has been deleted')
    except Exception as e:
        db.session.rollback()
        flash('Error deleting character. Please try again.')
        print(f"Error deleting character: {str(e)}")
    
    return redirect(url_for('my_characters'))

def update_event_statuses():
    """Update the status of all events based on their dates"""
    with app.app_context():
        now = datetime.now()
        
        # Update upcoming to in progress
        Event.query.filter(
            Event.status == 'Upcoming',
            Event.start_date <= now
        ).update({'status': 'In Progress'})
        
        # Update in progress to completed
        Event.query.filter(
            Event.status == 'In Progress',
            Event.end_date <= now
        ).update({'status': 'Completed'})
        
        db.session.commit()

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(func=update_event_statuses, trigger="interval", minutes=5)

# Start scheduler when app starts
with app.app_context():
    scheduler.start()

# Shut down the scheduler when exiting the app
atexit.register(lambda: scheduler.shutdown())

def init_db():
    with app.app_context():
        # Create tables if they don't exist
        db.create_all()
        
        # Add new columns to User table if they don't exist
        try:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE user ADD COLUMN first_name VARCHAR(80)"))
                conn.execute(text("ALTER TABLE user ADD COLUMN last_name VARCHAR(80)"))
                conn.execute(text("ALTER TABLE user ADD COLUMN phone VARCHAR(30)"))
                conn.execute(text("ALTER TABLE user ADD COLUMN address VARCHAR(200)"))
                conn.execute(text("ALTER TABLE user ADD COLUMN birthday DATE"))
                conn.commit()
        except Exception as e:
            print(f"Note: user columns may already exist: {str(e)}")
        
        # Add processed column to Event table if it doesn't exist
        try:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE event ADD COLUMN processed BOOLEAN DEFAULT FALSE"))
                conn.commit()
        except Exception as e:
            print(f"Note: processed column may already exist: {str(e)}")
        
        # Add group_name column to Character table if it doesn't exist
        try:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE character ADD COLUMN group_name VARCHAR(100)"))
                conn.commit()
        except Exception as e:
            print(f"Note: group_name column may already exist: {str(e)}")
        
        # Add resources column to Skill table if it doesn't exist
        try:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE skill ADD COLUMN resources INTEGER DEFAULT 0"))
                conn.commit()
        except Exception as e:
            print(f"Note: resources column may already exist: {str(e)}")
        
        # Load skills from Excel if not already loaded
        if Skill.query.count() == 0:
            try:
                print("Attempting to load skills from Excel...")
                df = pd.read_excel('skills.xlsx')
                print("Excel columns found:", df.columns.tolist())
                
                # Print first few rows for debugging
                print("\nFirst few rows of data:")
                print(df.head())
                
                # Verify required columns exist
                required_columns = ['Lore Category', 'Sub Category', 'Skill Name', 'Status']
                missing_columns = [col for col in required_columns if col not in df.columns]
                if missing_columns:
                    raise ValueError(f"Missing required columns in Excel file: {', '.join(missing_columns)}")
                
                # Process each row
                for index, row in df.iterrows():
                    try:
                        # Clean and validate data
                        lore_category = str(row['Lore Category']).strip()
                        sub_category = str(row['Sub Category']).strip()
                        skill_name = str(row['Skill Name']).strip()
                        
                        # Handle status value - try to convert to integer
                        try:
                            # First try to convert directly
                            cost = int(row['Status'])
                        except (ValueError, TypeError):
                            # If that fails, try to clean the string first
                            status_str = str(row['Status']).strip()
                            # Remove any non-numeric characters except decimal point
                            status_str = ''.join(c for c in status_str if c.isdigit() or c == '.')
                            if status_str:
                                cost = int(float(status_str))
                            else:
                                print(f"Warning: Invalid status value for skill '{skill_name}': {row['Status']}")
                                continue
                        
                        # Handle optional rank column
                        rank = None
                        if 'Rank' in df.columns and pd.notna(row.get('Rank')):
                            try:
                                rank = int(row['Rank'])
                            except (ValueError, TypeError):
                                print(f"Warning: Invalid rank value for skill '{skill_name}': {row.get('Rank')}")
                        
                        # Handle optional resources column
                        resources = 0
                        if 'Resources' in df.columns and pd.notna(row.get('Resources')):
                            try:
                                resources = int(row['Resources'])
                            except (ValueError, TypeError):
                                resources_str = str(row['Resources']).strip()
                                resources_str = ''.join(c for c in resources_str if c.isdigit() or c == '.')
                                if resources_str:
                                    resources = int(float(resources_str))
                        
                        # Create or update skill
                        existing_skill = Skill.query.filter_by(
                            lore_category=lore_category,
                            sub_category=sub_category,
                            name=skill_name
                        ).first()
                        if not existing_skill:
                            skill = Skill(
                                lore_category=lore_category,
                                sub_category=sub_category,
                                name=skill_name,
                                cost=cost,
                                rank=rank,
                                resources=resources
                            )
                            db.session.add(skill)
                            print(f"Added skill: {skill_name} ({lore_category} - {sub_category}) with cost {cost} and resources {resources}")
                        else:
                            # Update existing skill fields
                            existing_skill.cost = cost
                            existing_skill.rank = rank
                            existing_skill.resources = resources
                            print(f"Updated skill: {skill_name} ({lore_category} - {sub_category}) to cost {cost}, rank {rank}, resources {resources}")
                    
                    except Exception as row_error:
                        print(f"Error processing row {index + 2}: {str(row_error)}")
                        continue
                
                db.session.commit()
                print("\nSuccessfully loaded all skills from Excel file!")
                
            except Exception as e:
                print(f"Error loading skills: {str(e)}")
                db.session.rollback()
                raise

@app.route('/generate_character_pdf/<int:character_id>')
@login_required
def generate_character_pdf(character_id):
    character = Character.query.get_or_404(character_id)
    if character.user_id != current_user.id:
        return redirect(url_for('my_characters'))
    
    buffer = BytesIO()
    # Reduce all margins to 0.25 inches (18 points) to maximize usable space
    doc = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=18, rightMargin=18, topMargin=18, bottomMargin=18)
    styles = getSampleStyleSheet()
    
    # Create a smaller font style
    styles.add(ParagraphStyle(
        name='SmallText',
        parent=styles['Normal'],
        fontSize=8,
        leading=10
    ))
    
    # Create a smaller heading style
    styles.add(ParagraphStyle(
        name='SmallHeading',
        parent=styles['Heading2'],
        fontSize=10,
        leading=12,
        spaceAfter=2
    ))
    
    elements = []
    
    resources = get_character_resources(character)
    basic_info = [
        [
            ['Name:', character.name],
            ['Realm:', character.realm],
            ['Species:', character.species],
            ['Health:', str(character.health)],
            ['Stamina:', str(character.stamina)],
            ['Resources:', str(resources)],  # Show actual resources
        ],
        [
            ['Status Total:', str(character.total_status)],
            ['Status Spent:', str(character.status_spent)],
            ['Status Available:', str(character.status_remaining)],
            ['Group:', character.group_name or ''],
            ['Rank:', str(character.rank)],
            ['Character ID:', str(character.id)],
        ]
    ]
    
    # Calculate available width (letter width - margins)
    available_width = letter[0] - 36  # 36 points for both left and right margins
    column_width = (available_width - 10) / 2  # Divide available width by 2, leaving 10 points for spacing
    
    # Create tables for each column
    left_table = Table(basic_info[0], colWidths=[column_width * 0.3, column_width * 0.7])
    right_table = Table(basic_info[1], colWidths=[column_width * 0.3, column_width * 0.7])
    
    # Style for both tables
    table_style = [
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('BACKGROUND', (0, 0), (0, -1), colors.lightgrey),
        ('PADDING', (0, 0), (-1, -1), 4),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
    ]
    
    left_table.setStyle(TableStyle(table_style))
    right_table.setStyle(TableStyle(table_style))
    
    # Create a container for the two tables
    info_container = Table([[left_table, right_table]], colWidths=[column_width, column_width])
    info_container.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    
    elements.append(info_container)
    elements.append(Spacer(1, 2))
    
    # Skills section with smaller heading
    elements.append(Paragraph("Skills", styles['SmallHeading']))
    elements.append(Spacer(1, 1))
    
    # Get and organize skills by category
    character_skills = CharacterSkill.query.filter_by(character_id=character.id).all()
    skills_by_category = {}
    
    for cs in character_skills:
        skill = cs.skill
        if skill.lore_category not in skills_by_category:
            skills_by_category[skill.lore_category] = []
        rank_str = str(skill.rank) if skill.rank is not None else "-"
        skills_by_category[skill.lore_category].append([skill.name, rank_str])
    
    # Create continuous flow of skills across columns
    MAX_ROWS = 37
    total_columns = 5
    all_skills = []
    
    # Flatten skills into a single list with category headers
    for category, skills in skills_by_category.items():
        all_skills.append([category, ""])  # Add category header
        all_skills.extend(skills)  # Add all skills for this category
    
    # Create columns of MAX_ROWS each
    columns = []
    current_column = []
    
    for skill in all_skills:
        if len(current_column) >= MAX_ROWS:
            columns.append(current_column)
            current_column = []
        current_column.append(skill)
    
    if current_column:  # Add the last column if it's not empty
        columns.append(current_column)
    
    # Ensure we have exactly 5 columns
    while len(columns) < total_columns:
        columns.append([["", ""] for _ in range(MAX_ROWS)])
    
    # Pad all columns to MAX_ROWS
    for col in columns:
        while len(col) < MAX_ROWS:
            col.append(["", ""])
    
    # Create the table data
    table_data = []
    for row in range(MAX_ROWS):
        row_data = []
        for col in columns:
            row_data.extend(col[row])
        table_data.append(row_data)
    
    # Calculate column widths to fill the available space
    # Each column pair (skill name + rank) gets equal width
    column_pair_width = available_width / total_columns
    skill_width = column_pair_width * 0.85  # 85% for skill name
    rank_width = column_pair_width * 0.15   # 15% for rank
    
    # Create the column widths list
    col_widths = []
    for _ in range(total_columns):
        col_widths.extend([skill_width, rank_width])
    
    # Create a list to track which cells are category headers
    category_cells = []
    for row_idx, row in enumerate(table_data):
        for col_idx in range(0, len(row), 2):  # Check every skill name cell
            if row[col_idx] in skills_by_category.keys():  # If it's a category name
                category_cells.append((row_idx, col_idx))
    
    # Create the table
    skills_table = Table(table_data, colWidths=col_widths)
    
    # Create the table style
    table_style = [
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('PADDING', (0, 0), (-1, -1), 2),
        ('ALIGN', (1, 0), (1, -1), 'CENTER'),
        ('ALIGN', (3, 0), (3, -1), 'CENTER'),
        ('ALIGN', (5, 0), (5, -1), 'CENTER'),
        ('ALIGN', (7, 0), (7, -1), 'CENTER'),
        ('ALIGN', (9, 0), (9, -1), 'CENTER'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('LEADING', (0, 0), (-1, -1), 10),
    ]
    
    # Add background color for category cells
    for row, col in category_cells:
        table_style.append(('BACKGROUND', (col, row), (col + 1, row), colors.lightgrey))
    
    skills_table.setStyle(TableStyle(table_style))
    elements.append(skills_table)
    
    doc.build(elements)
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"{character.name}_character_sheet.pdf",
        mimetype='application/pdf'
    )

@app.route('/clear_completed_events', methods=['POST'])
@login_required
def clear_completed_events():
    # Delete all completed events and their participations
    completed_events = Event.query.filter_by(status='Completed').all()
    
    for event in completed_events:
        # Delete all cast signups for this event first
        CastSignup.query.filter_by(event_id=event.id).delete()
        # Delete all participations for this event
        EventParticipation.query.filter_by(event_id=event.id).delete()
        # Delete the event
        db.session.delete(event)
    
    db.session.commit()
    flash('Completed events have been cleared')
    return redirect(url_for('events'))

@app.route('/admin/permissions')
@login_required
def admin_permissions():
    # Check if user has admin or moderator permissions
    if not (current_user.is_admin or current_user.is_moderator):
        flash('You do not have permission to access this page')
        return redirect(url_for('my_characters'))
    
    # Get search parameters
    email_search = request.args.get('email_search', '')
    permission_filter = request.args.get('permission_filter', '')
    
    # Start with base query
    query = User.query
    
    # Apply email search filter
    if email_search:
        query = query.filter(User.email.ilike(f'%{email_search}%'))
    
    # Apply permission filter
    if permission_filter:
        query = query.filter(getattr(User, permission_filter) == True)
    
    # Get all users
    users = query.all()
    
    return render_template('admin_permissions.html', users=users, is_admin=current_user.is_admin)

@app.route('/admin/user/<int:user_id>/update_permission', methods=['POST'])
@login_required
def update_user_permission(user_id):
    # Check if user has admin or moderator permissions
    if not (current_user.is_admin or current_user.is_moderator):
        return jsonify({'success': False, 'message': 'You do not have permission to perform this action'})
    
    user = User.query.get_or_404(user_id)
    permission = request.form.get('permission')
    value = request.form.get('value') == 'true'
    
    # Only admins can modify admin and moderator status
    if permission in ['is_admin', 'is_moderator'] and not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Only administrators can modify admin and moderator status'})
    
    # Update the permission
    if permission in ['is_admin', 'is_moderator', 'can_create_events', 'can_add_event_status', 'can_adjust_character_status', 'can_accept_cast']:
        setattr(user, permission, value)
        db.session.commit()
        return jsonify({'success': True, 'message': f'Successfully updated {permission} for {user.email}'})
    else:
        return jsonify({'success': False, 'message': 'Invalid permission specified'})

@app.route('/admin/user/<int:user_id>/details')
@login_required
def view_user_details(user_id):
    # Check if user has admin or moderator permissions
    if not (current_user.is_admin or current_user.is_moderator):
        flash('You do not have permission to access this page')
        return redirect(url_for('my_characters'))
    
    user = User.query.get_or_404(user_id)
    characters = Character.query.filter_by(user_id=user.id).all()
    
    return render_template('user_details.html', user=user, characters=characters)

@app.route('/setup_admin')
def setup_admin():
    # Check if any admin exists
    admin_exists = User.query.filter_by(is_admin=True).first()
    if admin_exists:
        flash('Admin user already exists')
        return redirect(url_for('login'))
    
    # Get the first user and make them admin
    first_user = User.query.first()
    if first_user:
        first_user.is_admin = True
        db.session.commit()
        flash(f'User {first_user.email} has been set as admin')
    else:
        flash('No users found')
    
    return redirect(url_for('login'))

@app.route('/get_cast_signups/<int:event_id>')
@login_required
def get_cast_signups(event_id):
    if not current_user.can_accept_cast:
        flash('You do not have permission to manage cast signups')
        return redirect(url_for('status_management'))
    
    event = Event.query.get_or_404(event_id)
    
    # Get all pending cast signups for this event
    cast_signups = CastSignup.query.filter_by(
        event_id=event_id,
        status='Pending'
    ).order_by(CastSignup.timeblock).all()
    
    return render_template('cast_signups.html',
                         event=event,
                         cast_signups=cast_signups)

@app.route('/process_cast_signup', methods=['POST'])
@login_required
def process_cast_signup():
    if not current_user.can_accept_cast:
        flash('You do not have permission to manage cast signups')
        return redirect(url_for('status_management'))
    
    cast_signup_id = request.form.get('cast_signup_id')
    action = request.form.get('action')
    writing_status = int(request.form.get('writing_status', 0))
    management_status = int(request.form.get('management_status', 0))
    notes = request.form.get('notes', '')
    
    cast_signup = CastSignup.query.get_or_404(cast_signup_id)
    
    if action == 'accept':
        cast_signup.status = 'Accepted'
        cast_signup.writing_status = writing_status
        cast_signup.management_status = management_status
        cast_signup.notes = notes
        
        # Add status adjustments
        character = cast_signup.character
        
        # Add cast status (100 points)
        cast_adjustment = StatusAdjustment(
            character_id=character.id,
            amount=100,
            status_type='Cast',
            notes=f'Event: {cast_signup.event.title} - Timeblock {cast_signup.timeblock}',
            adjusted_by=current_user.id
        )
        db.session.add(cast_adjustment)
        
        # Add writing status if any
        if writing_status > 0:
            writing_adjustment = StatusAdjustment(
                character_id=character.id,
                amount=writing_status,
                status_type='Writing',
                notes=f'Event: {cast_signup.event.title} - Timeblock {cast_signup.timeblock}',
                adjusted_by=current_user.id
            )
            db.session.add(writing_adjustment)
        
        # Add management status if any
        if management_status > 0:
            management_adjustment = StatusAdjustment(
                character_id=character.id,
                amount=management_status,
                status_type='Management',
                notes=f'Event: {cast_signup.event.title} - Timeblock {cast_signup.timeblock}',
                adjusted_by=current_user.id
            )
            db.session.add(management_adjustment)
        
        # Update character's total status
        total_status = 100 + writing_status + management_status
        character.total_status += total_status
        character.status_remaining += total_status
        character.update_rank()
        
    elif action == 'deny':
        cast_signup.status = 'Denied'
        cast_signup.notes = notes
    
    db.session.commit()
    flash(f'Successfully {action}ed cast signup')
    return redirect(url_for('get_cast_signups', event_id=cast_signup.event_id))

@app.route('/recreate_db')
def recreate_db():
    """Route to recreate the database (for development only)"""
    if not current_user.is_authenticated or not current_user.is_admin:
        flash('You do not have permission to perform this action')
        return redirect(url_for('home'))
    
    init_db()
    flash('Database has been recreated')
    return redirect(url_for('home'))

@app.route('/event/<int:event_id>/my_signups')
@login_required
def my_event_signups(event_id):
    event = Event.query.get_or_404(event_id)
    
    # Get participant signups
    participant_signups = EventParticipation.query.filter_by(
        event_id=event_id,
        user_id=current_user.id
    ).all()
    
    # Get cast signups
    cast_signups = CastSignup.query.filter_by(
        event_id=event_id,
        user_id=current_user.id
    ).all()
    
    # Combine and sort all signups by timeblock
    all_signups = []
    
    for signup in participant_signups:
        all_signups.append({
            'timeblock': signup.timeblock,
            'type': 'Participant',
            'character': signup.character.name,
            'status': 'Confirmed'
        })
    
    for signup in cast_signups:
        all_signups.append({
            'timeblock': signup.timeblock,
            'type': 'Cast',
            'character': signup.character.name,
            'status': signup.status
        })
    
    # Sort by timeblock
    all_signups.sort(key=lambda x: x['timeblock'])
    
    return render_template('my_event_signups.html',
                         event=event,
                         signups=all_signups)

@app.route('/event/<int:event_id>/roster')
@login_required
def event_roster(event_id):
    if not current_user.can_create_events:
        flash('You do not have permission to view this page')
        return redirect(url_for('events'))
    event = Event.query.get_or_404(event_id)
    # Group participants by (user, character)
    participants = EventParticipation.query.filter_by(event_id=event_id).all()
    participant_groups = {}
    for p in participants:
        key = (p.user_id, p.character_id)
        if key not in participant_groups:
            participant_groups[key] = {'user': p.user, 'character': p.character, 'timeblocks': []}
        participant_groups[key]['timeblocks'].append(p.timeblock)
    # Convert to list and sort timeblocks
    participant_groups = [
        {'user': v['user'], 'character': v['character'], 'timeblocks': sorted(v['timeblocks'])}
        for v in participant_groups.values()
    ]
    # Group cast signups by (user, character)
    cast_signups = CastSignup.query.filter_by(event_id=event_id).all()
    cast_groups = {}
    for c in cast_signups:
        key = (c.user_id, c.character_id)
        if key not in cast_groups:
            cast_groups[key] = {'user': c.user, 'character': c.character, 'timeblocks': [], 'statuses': []}
        cast_groups[key]['timeblocks'].append(c.timeblock)
        cast_groups[key]['statuses'].append(c.status)
    # Convert to list and sort timeblocks
    cast_groups = [
        {'user': v['user'], 'character': v['character'], 'timeblocks': sorted(v['timeblocks']), 'statuses': v['statuses']}
        for v in cast_groups.values()
    ]
    return render_template('event_roster.html', event=event, participant_groups=participant_groups, cast_groups=cast_groups)

@app.route('/event/<int:event_id>/roster_pdf')
@login_required
def event_roster_pdf(event_id):
    if not current_user.can_create_events:
        flash('You do not have permission to view this page')
        return redirect(url_for('events'))
    event = Event.query.get_or_404(event_id)
    # Group participants by (user, character)
    participants = EventParticipation.query.filter_by(event_id=event_id).all()
    participant_groups = {}
    for p in participants:
        key = (p.user_id, p.character_id)
        if key not in participant_groups:
            participant_groups[key] = {'user': p.user, 'character': p.character, 'timeblocks': []}
        participant_groups[key]['timeblocks'].append(p.timeblock)
    participant_groups = [
        {'user': v['user'], 'character': v['character'], 'timeblocks': sorted(v['timeblocks'])}
        for v in participant_groups.values()
    ]
    # Group cast signups by (user, character)
    cast_signups = CastSignup.query.filter_by(event_id=event_id).all()
    cast_groups = {}
    for c in cast_signups:
        key = (c.user_id, c.character_id)
        if key not in cast_groups:
            cast_groups[key] = {'user': c.user, 'character': c.character, 'timeblocks': [], 'statuses': []}
        cast_groups[key]['timeblocks'].append(c.timeblock)
        cast_groups[key]['statuses'].append(c.status)
    cast_groups = [
        {'user': v['user'], 'character': v['character'], 'timeblocks': sorted(v['timeblocks']), 'statuses': v['statuses']}
        for v in cast_groups.values()
    ]
    # Generate PDF
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    elements = []
    elements.append(Paragraph(f"Event Roster: {event.title}", styles['Title']))
    elements.append(Paragraph(f"Realm: {event.realm}", styles['Normal']))
    elements.append(Paragraph(f"Location: {event.location}", styles['Normal']))
    elements.append(Paragraph(f"Start: {event.start_date.strftime('%Y-%m-%d %I:%M %p')}", styles['Normal']))
    elements.append(Paragraph(f"End: {event.end_date.strftime('%Y-%m-%d %I:%M %p')}", styles['Normal']))
    elements.append(Spacer(1, 12))
    # Participants section
    elements.append(Paragraph("Participants", styles['Heading2']))
    if participant_groups:
        data = [["User", "Character", "Timeblocks"]]
        for group in participant_groups:
            data.append([
                f"{group['user'].email}",
                f"{group['character'].name} ({group['character'].realm})",
                ', '.join(str(tb) for tb in group['timeblocks'])
            ])
        table = Table(data, hAlign='LEFT')
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(table)
    else:
        elements.append(Paragraph("No participants.", styles['Normal']))
    elements.append(Spacer(1, 16))
    # Cast section
    elements.append(Paragraph("Cast", styles['Heading2']))
    if cast_groups:
        data = [["User", "Character", "Timeblocks", "Status"]]
        for group in cast_groups:
            data.append([
                f"{group['user'].email}",
                f"{group['character'].name} ({group['character'].realm})",
                ', '.join(str(tb) for tb in group['timeblocks']),
                ', '.join(group['statuses'])
            ])
        table = Table(data, hAlign='LEFT')
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(table)
    else:
        elements.append(Paragraph("No cast signups.", styles['Normal']))
    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"{event.title}_roster.pdf", mimetype='application/pdf')

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = current_user
    if request.method == 'POST':
        user.first_name = request.form.get('first_name', user.first_name)
        user.last_name = request.form.get('last_name', user.last_name)
        user.phone = request.form.get('phone', user.phone)
        user.address = request.form.get('address', user.address)
        birthday_str = request.form.get('birthday')
        from datetime import datetime
        if birthday_str:
            user.birthday = datetime.strptime(birthday_str, '%Y-%m-%d').date()
        db.session.commit()
        flash('Profile updated successfully!')
        return redirect(url_for('profile'))
    return render_template('profile.html', user=user)

if __name__ == '__main__':
    # Initialize the database (create tables if they don't exist)
    init_db()
    app.run(debug=True) 