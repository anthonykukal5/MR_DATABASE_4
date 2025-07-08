from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file
from flask_login import login_required, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db
from datetime import datetime, UTC
from io import BytesIO
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from models import User, Character, CharacterSkill, Skill, StatusAdjustment, StatusPurchase, EventParticipation, CastSignup

users_bp = Blueprint('users', __name__)

# Add the mapping here instead:
SPECIES_BY_REALM = {
    'Everstars': ['Human', 'Android', 'Gen-E'],
    'Guildhall': ['Human', 'Elf', 'Orc'],
    'Tyrs': ['Human', 'Ghoul', 'Airadin']
}

# User and character routes will be added here 

@users_bp.route('/my_characters')
@login_required
def my_characters():
    characters = Character.query.filter_by(user_id=current_user.id).all()
    return render_template('my_characters.html', characters=characters)

@users_bp.route('/membership')
@login_required
def membership():
    return render_template('membership.html')

@users_bp.route('/membership/subscribe', methods=['GET', 'POST'])
@login_required
def subscribe_membership():
    if request.method == 'POST':
        membership_level = request.form.get('membership_level')
        
        if membership_level not in ['Basic', 'Standard', 'Premium']:
            flash('Invalid membership level')
            return redirect(url_for('users.membership'))
        
        # Set membership level and expiry (1 year from now)
        current_user.membership_level = membership_level
        current_user.membership_expiry = datetime.now(UTC).replace(year=datetime.now(UTC).year + 1)
        
        db.session.commit()
        flash(f'Successfully subscribed to {membership_level} membership!')
        return redirect(url_for('users.membership'))
    
    return render_template('subscribe_membership.html')

@users_bp.route('/membership/upgrade', methods=['GET', 'POST'])
@login_required
def upgrade_membership():
    if current_user.membership_level == 'None':
        flash('You must have an active membership to upgrade')
        return redirect(url_for('users.membership'))
    
    if request.method == 'POST':
        new_level = request.form.get('membership_level')
        
        if new_level not in ['Standard', 'Premium']:
            flash('Invalid membership level')
            return redirect(url_for('users.membership'))
        
        # Check if it's actually an upgrade
        levels = ['None', 'Basic', 'Standard', 'Premium']
        current_index = levels.index(current_user.membership_level)
        new_index = levels.index(new_level)
        
        if new_index <= current_index:
            flash('You can only upgrade to a higher membership level')
            return redirect(url_for('users.membership'))
        
        # Update membership level, keep existing expiry date
        current_user.membership_level = new_level
        
        db.session.commit()
        flash(f'Successfully upgraded to {new_level} membership!')
        return redirect(url_for('users.membership'))
    
    return render_template('upgrade_membership.html')

@users_bp.route('/membership/cancel', methods=['POST'])
@login_required
def cancel_membership():
    if current_user.membership_level == 'None':
        flash('You do not have an active membership to cancel')
        return redirect(url_for('users.membership'))
    
    # Set membership to None and clear expiry
    current_user.membership_level = 'None'
    current_user.membership_expiry = None
    
    db.session.commit()
    flash('Your membership has been cancelled. You can still view your characters but cannot edit them.')
    return redirect(url_for('users.membership'))

@users_bp.route('/create_character', methods=['GET', 'POST'])
@login_required
def create_character():
    # Check character limit
    current_character_count = len(current_user.characters)
    character_limit = current_user.get_character_limit()
    
    if current_character_count >= character_limit:
        flash(f'You have reached your character limit of {character_limit} characters for your {current_user.membership_level} membership level.')
        return redirect(url_for('users.my_characters'))
    
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
        
        # Redirect to creating character page to spend initial status
        return redirect(url_for('users.creating_character', character_id=character.id))
    
    return render_template('create_character.html')

@users_bp.route('/creating_character/<int:character_id>', methods=['GET', 'POST'])
@login_required
def creating_character(character_id):
    character = Character.query.get_or_404(character_id)
    if character.user_id != current_user.id:
        return redirect(url_for('users.my_characters'))
    
    if request.method == 'POST':
        try:
            character.name = request.form.get('name')
            character.species = request.form.get('species')
            character.group_name = request.form.get('group')
            character.health = int(request.form.get('health', 0))
            character.stamina = int(request.form.get('stamina', 0))
            total_spent = 0
            total_spent += character.health * 200
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
            selected_skills = request.form.getlist('skills')
            CharacterSkill.query.filter_by(character_id=character.id).delete()
            for skill_id in selected_skills:
                skill = Skill.query.get(skill_id)
                if skill:
                    character_skill = CharacterSkill(
                        character_id=character.id,
                        skill_id=skill.id
                    )
                    db.session.add(character_skill)
                    total_spent += skill.cost
            character.status_spent = total_spent
            character.status_remaining = character.total_status - total_spent
            character.update_rank()
            if total_spent > character.total_status:
                flash('Error: Total status spent exceeds available status points')
                return redirect(url_for('users.creating_character', character_id=character.id))
            db.session.commit()
            flash('Character created successfully!')
            return redirect(url_for('users.my_characters'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating character: {str(e)}')
            return redirect(url_for('users.creating_character', character_id=character.id))
    
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
    character_skills = [cs.skill_id for cs in character.skills]
    return render_template('creating_character.html',
                         character=character,
                         species_by_realm=SPECIES_BY_REALM,
                         skills_by_category=skills_by_category,
                         skills_by_subcategory=skills_by_subcategory,
                         character_skills=character_skills)

@users_bp.route('/edit_character/<int:character_id>', methods=['GET', 'POST'])
@login_required
def edit_character(character_id):
    character = Character.query.get_or_404(character_id)
    if character.user_id != current_user.id:
        return redirect(url_for('users.my_characters'))
    
    # Check if user can edit this character based on membership
    editable_characters = current_user.get_editable_characters()
    if character not in editable_characters:
        flash('You cannot edit this character with your current membership level. Please upgrade your membership to edit more characters.')
        return redirect(url_for('users.view_character', character_id=character.id))
    if request.method == 'POST':
        try:
            character.name = request.form.get('name')
            character.species = request.form.get('species')
            character.group_name = request.form.get('group')
            character.health = int(request.form.get('health', 0))
            character.stamina = int(request.form.get('stamina', 0))
            total_spent = 0
            total_spent += character.health * 200
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
            selected_skills = request.form.getlist('skills')
            CharacterSkill.query.filter_by(character_id=character.id).delete()
            for skill_id in selected_skills:
                skill = Skill.query.get(skill_id)
                if skill:
                    character_skill = CharacterSkill(
                        character_id=character.id,
                        skill_id=skill.id
                    )
                    db.session.add(character_skill)
                    total_spent += skill.cost
            character.status_spent = total_spent
            character.status_remaining = character.total_status - total_spent
            character.update_rank()
            if total_spent > character.total_status:
                flash('Error: Total status spent exceeds available status points')
                return redirect(url_for('users.edit_character', character_id=character.id))
            db.session.commit()
            flash('Character updated successfully!')
            return redirect(url_for('users.my_characters'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating character: {str(e)}')
            return redirect(url_for('users.edit_character', character_id=character.id))
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
    character_skills = [cs.skill_id for cs in character.skills]
    return render_template('edit_character.html',
                         character=character,
                         species_by_realm=SPECIES_BY_REALM,
                         skills_by_category=skills_by_category,
                         skills_by_subcategory=skills_by_subcategory,
                         character_skills=character_skills)

@users_bp.route('/view_character/<int:character_id>')
@login_required
def view_character(character_id):
    character = Character.query.get_or_404(character_id)
    if character.user_id != current_user.id:
        return redirect(url_for('users.my_characters'))
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
    character_skills = [cs.skill_id for cs in character.skills]
    resources = sum(cs.skill.resources or 0 for cs in character.skills)
    return render_template('view_character.html',
                         character=character,
                         skills_by_category=skills_by_category,
                         skills_by_subcategory=skills_by_subcategory,
                         character_skills=character_skills,
                         resources=resources)

@users_bp.route('/character/<int:character_id>/status_history')
@login_required
def character_status_history(character_id):
    character = Character.query.get_or_404(character_id)
    if character.user_id != current_user.id:
        flash('You do not have permission to view this character\'s history')
        return redirect(url_for('users.my_characters'))
    adjustments = StatusAdjustment.query.filter_by(character_id=character_id).all()
    adjustment_history = [{
        'date': adj.date,
        'amount': adj.amount,
        'reason': adj.status_type,
        'notes': adj.notes,
        'source': 'Manual Adjustment'
    } for adj in adjustments]
    participations = []  # Fill if needed
    status_history = adjustment_history + participations
    status_history.sort(key=lambda x: x['date'])
    status_totals = {k: 0 for k in ['Writing', 'Management', 'Service', 'Cast', 'Interaction', 'Play']}
    for adj in adjustments:
        if adj.status_type in status_totals:
            status_totals[adj.status_type] += adj.amount
    return render_template('character_status_history.html',
                         character=character,
                         status_history=status_history,
                         status_totals=status_totals)

@users_bp.route('/adjust_character_status', methods=['POST'])
@login_required
def adjust_character_status():
    character_search = request.form.get('character_search', '').strip()
    status_amount = int(request.form.get('status_amount', 0))
    status_type = request.form.get('status_type')
    notes = request.form.get('notes')
    if request.form.get('action') == 'search':
        if not character_search:
            flash('Please enter a character name or ID to search')
            return redirect(url_for('events.status_management'))
        try:
            character_id = int(character_search)
            characters = Character.query.filter_by(id=character_id).all()
        except ValueError:
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
    character_id = request.form.get('character_id')
    if not character_id:
        flash('Please select a character to adjust status')
        return redirect(url_for('events.status_management'))
    character = Character.query.get_or_404(character_id)
    adjustment = StatusAdjustment(
        character_id=character.id,
        amount=status_amount,
        status_type=status_type,
        notes=notes,
        adjusted_by=current_user.id
    )
    character.total_status += status_amount
    character.status_remaining += status_amount
    character.update_rank()
    db.session.add(adjustment)
    db.session.commit()
    flash(f'Successfully adjusted status for {character.name}')
    return redirect(url_for('events.status_management'))

@users_bp.route('/purchase_status', methods=['POST'])
@login_required
def purchase_status():
    character_id = request.form.get('character_id')
    character = Character.query.get_or_404(character_id)
    if character.user_id != current_user.id:
        flash('Invalid character selection')
        return redirect(url_for('events.status_management'))
    purchase = StatusPurchase(
        character_id=character.id,
        status='Pending'
    )
    db.session.add(purchase)
    db.session.commit()
    flash('Status purchase request submitted. Payment processing will be implemented soon.')
    return redirect(url_for('events.status_management'))

@users_bp.route('/delete_character/<int:character_id>', methods=['POST'])
@login_required
def delete_character(character_id):
    character = Character.query.get_or_404(character_id)
    if character.user_id != current_user.id:
        flash('You do not have permission to delete this character')
        return redirect(url_for('users.my_characters'))
    try:
        CharacterSkill.query.filter_by(character_id=character.id).delete()
        StatusAdjustment.query.filter_by(character_id=character.id).delete()
        StatusPurchase.query.filter_by(character_id=character.id).delete()
        EventParticipation.query.filter_by(character_id=character.id).delete()
        CastSignup.query.filter_by(character_id=character.id).delete()
        db.session.delete(character)
        db.session.commit()
        flash(f'Character {character.name} has been deleted')
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting character: {str(e)}")
        flash('Error deleting character. Please try again.')
    return redirect(url_for('users.my_characters'))

@users_bp.route('/admin/permissions')
@login_required
def admin_permissions():
    # Check if user has admin or moderator permissions
    if not (current_user.is_admin or current_user.is_moderator):
        flash('You do not have permission to access this page')
        return redirect(url_for('users.my_characters'))
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

@users_bp.route('/admin/membership/<int:user_id>', methods=['GET', 'POST'])
@login_required
def admin_manage_membership(user_id):
    # Check if user has admin permissions
    if not current_user.is_admin:
        flash('You do not have permission to access this page')
        return redirect(url_for('users.my_characters'))
    
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        membership_level = request.form.get('membership_level')
        membership_expiry = request.form.get('membership_expiry')
        
        if membership_level not in ['None', 'Basic', 'Standard', 'Premium']:
            flash('Invalid membership level')
            return redirect(url_for('users.admin_manage_membership', user_id=user_id))
        
        user.membership_level = membership_level
        
        if membership_expiry:
            try:
                user.membership_expiry = datetime.strptime(membership_expiry, '%Y-%m-%d')
            except ValueError:
                flash('Invalid expiry date format')
                return redirect(url_for('users.admin_manage_membership', user_id=user_id))
        else:
            user.membership_expiry = None
        
        db.session.commit()
        flash(f'Membership updated for {user.first_name} {user.last_name}')
        return redirect(url_for('users.admin_permissions'))
    
    return render_template('admin_manage_membership.html', user=user)

