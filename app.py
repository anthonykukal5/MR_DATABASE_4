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
from main_routes import main_routes_bp
from events import events_bp
from users import users_bp
from skills import skills_bp
from models import User, Character, CharacterSkill, StatusAdjustment, StatusPurchase, Skill, Event, EventParticipation, CastSignup
from extensions import db, login_manager
from arbitration import arbitration_bp

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

def get_character_resources(character):
    """Return the sum of resources for all skills the character has."""
    return sum(cs.skill.resources or 0 for cs in character.skills)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'  # Change this to a secure secret key
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://myuser:mypassword@localhost/mystic_realms'

# Add timezone to template context
@app.context_processor
def inject_timezone():
    return {'EST': EST}

db.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'login'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Register blueprints
app.register_blueprint(main_routes_bp)
app.register_blueprint(events_bp, url_prefix='/events')
app.register_blueprint(users_bp, url_prefix='/users')
app.register_blueprint(skills_bp, url_prefix='/skills')
app.register_blueprint(arbitration_bp, url_prefix='/arbitration')

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
    if permission in ['is_admin', 'is_moderator', 'can_create_events', 'can_add_event_status', 'can_adjust_character_status', 'can_accept_cast', 'can_arbitrate']:
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
    back_url = request.referrer or url_for('users.admin_permissions')
    return render_template('user_details.html', user=user, characters=characters, back_url=back_url)

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
            adjusted_by=current_user.id,
            event_id=cast_signup.event_id
        )
        db.session.add(cast_adjustment)
        
        # Add writing status if any
        if writing_status > 0:
            writing_adjustment = StatusAdjustment(
                character_id=character.id,
                amount=writing_status,
                status_type='Writing',
                notes=f'Event: {cast_signup.event.title} - Timeblock {cast_signup.timeblock}',
                adjusted_by=current_user.id,
                event_id=cast_signup.event_id
            )
            db.session.add(writing_adjustment)
        
        # Add management status if any
        if management_status > 0:
            management_adjustment = StatusAdjustment(
                character_id=character.id,
                amount=management_status,
                status_type='Management',
                notes=f'Event: {cast_signup.event.title} - Timeblock {cast_signup.timeblock}',
                adjusted_by=current_user.id,
                event_id=cast_signup.event_id
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

@app.route('/admin/print_users')
@login_required
def print_users_pdf():
    if not (current_user.is_admin or current_user.is_moderator):
        flash('You do not have permission to access this page')
        return redirect(url_for('admin_permissions'))
    permission = request.args.get('permission', '')
    # Build query
    if permission and hasattr(User, permission):
        users = User.query.filter(getattr(User, permission) == True).all()
        filter_label = permission.replace('_', ' ').title()
    else:
        users = User.query.all()
        filter_label = 'All Users'
    # PDF generation
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    elements = []
    elements.append(Paragraph(f"User List", styles['Title']))
    elements.append(Paragraph(f"Filter: {filter_label}", styles['Heading2']))
    elements.append(Spacer(1, 12))
    data = [["First Name", "Last Name", "Email", "Date Registered", "Birthday"]]
    for user in users:
        date_registered = user.date_registered.strftime('%Y-%m-%d') if user.date_registered else ''
        birthday = user.birthday.strftime('%Y-%m-%d') if user.birthday else ''
        data.append([user.first_name, user.last_name, user.email, date_registered, birthday])
    table = Table(data, hAlign='LEFT')
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(table)
    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"user_list_{permission or 'all'}.pdf", mimetype='application/pdf')

@app.template_filter('phone_format')
def phone_format(value):
    if value and len(value) == 10 and value.isdigit():
        return f"({value[:3]}) {value[3:6]}-{value[6:]}"
    return value

if __name__ == '__main__':
    # Initialize the database (create tables if they don't exist)
    init_db()
    app.run(debug=True) 