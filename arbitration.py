from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
import pandas as pd
import os

arbitration_bp = Blueprint('arbitration', __name__)

OFFENSES_XLSX = os.path.join(os.path.dirname(__file__), 'offenses.xlsx')

def load_offenses():
    try:
        df = pd.read_excel(OFFENSES_XLSX)
        offenses = []
        for _, row in df.iterrows():
            offense = str(row.get('Offense', '')).strip()
            penalty = str(row.get('Penalty', '')).strip()
            if offense:
                offenses.append({'offense': offense, 'penalty': penalty})
        return offenses
    except Exception as e:
        print(f"Error loading offenses: {e}")
        return []

@arbitration_bp.route('/')
@login_required
def index():
    from models import Complaint, User
    # Admins and moderators see all complaints
    if current_user.is_admin or current_user.is_moderator:
        complaints = Complaint.query.order_by(Complaint.date_filed.desc()).all()
    # Arbitrators see only unresolved complaints
    elif current_user.can_arbitrate:
        complaints = Complaint.query.filter(Complaint.status != 'Resolved').order_by(Complaint.date_filed.desc()).all()
    else:
        complaints = []
    return render_template('arbitration.html', complaints=complaints)

@arbitration_bp.route('/create_complaint', methods=['GET', 'POST'])
@login_required
def create_complaint():
    offenses = load_offenses()
    if request.method == 'POST':
        accused_name = (request.form.get('accused_name') or '').strip()
        offense = request.form.get('offense')
        date_of_offense = request.form.get('date_of_offense')
        description = request.form.get('description')
        resolution_attempt = request.form.get('resolution_attempt')
        people_involved = request.form.get('people_involved')
        # Find accused user by first and last name
        accused_user = None
        if accused_name:
            parts = accused_name.split()
            if len(parts) >= 2:
                first, last = parts[0], ' '.join(parts[1:])
                from models import User, Complaint
                accused_user = User.query.filter(
                    User.first_name.ilike(first),
                    User.last_name.ilike(last)
                ).first()
        if not accused_user:
            flash('No user found with that name. Please check the spelling and try again.', 'danger')
            return render_template('create_complaint.html', offenses=offenses)
        # Get penalty for selected offense
        penalty = ''
        for o in offenses:
            if o['offense'] == offense:
                penalty = o['penalty']
                break
        # Save complaint
        from models import Complaint
        complaint = Complaint(
            complainant_id=current_user.id,
            accused_id=accused_user.id,
            offense=offense,
            penalty=penalty,
            date_of_offense=date_of_offense,
            description=description,
            resolution_attempt=resolution_attempt,
            people_involved=people_involved
        )
        from extensions import db
        db.session.add(complaint)
        db.session.commit()
        flash('Complaint submitted successfully.', 'success')
        return redirect(url_for('arbitration.index'))
    return render_template('create_complaint.html', offenses=offenses)

@arbitration_bp.route('/complaint/<int:complaint_id>', methods=['GET', 'POST'])
@login_required
def complaint_detail(complaint_id):
    from models import Complaint, User
    from extensions import db
    complaint = Complaint.query.get_or_404(complaint_id)
    users = User.query.all()
    # Allow arbitrators, admins, and moderators
    if not (current_user.can_arbitrate or current_user.is_admin or current_user.is_moderator):
        abort(403)
    # Arbitrator signup
    if request.method == 'POST' and 'signup' in request.form:
        if complaint.arbitrator_id is None:
            complaint.arbitrator_id = current_user.id
            db.session.commit()
            flash('You are now assigned as arbitrator for this complaint.', 'success')
        return redirect(url_for('arbitration.complaint_detail', complaint_id=complaint.id))
    # Resolution
    if request.method == 'POST' and 'resolve' in request.form and complaint.arbitrator_id == current_user.id:
        resolution = request.form.get('resolve')
        reason = request.form.get('resolution_reason')
        character_id = request.form.get('character_id')
        deduction_amount = request.form.get('deduction_amount')
        if resolution == 'Accepted':
            # Must select a character and valid deduction amount
            if not character_id:
                flash('You must select a character to deduct status from.', 'danger')
                return render_template('complaint_detail.html', complaint=complaint, users=users)
            try:
                penalty = int(complaint.penalty)
                deduction = int(deduction_amount)
            except Exception:
                penalty = 0
                deduction = 0
            if deduction < 1 or deduction > penalty:
                flash(f'Deduction amount must be between 1 and {penalty}.', 'danger')
                return render_template('complaint_detail.html', complaint=complaint, users=users)
            from models import Character, StatusAdjustment
            character = Character.query.get(character_id)
            if character and deduction > 0:
                character.total_status -= deduction
                character.status_remaining -= deduction
                character.update_rank()
                adjustment = StatusAdjustment(
                    character_id=character.id,
                    amount=-deduction,
                    status_type='Penalty',
                    notes=f'Arbitration Complaint #{complaint.id}: {complaint.offense}',
                    adjusted_by=current_user.id
                )
                db.session.add(adjustment)
        if resolution in ['Accepted', 'Denied'] and reason:
            complaint.resolution = resolution
            complaint.resolution_reason = reason
            complaint.status = 'Resolved'
            db.session.commit()
            flash('Complaint resolved.', 'success')
            return redirect(url_for('arbitration.index'))
        else:
            flash('Please provide a resolution and reason.', 'danger')
    return render_template('complaint_detail.html', complaint=complaint, users=users) 