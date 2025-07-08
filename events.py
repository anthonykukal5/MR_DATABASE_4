from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, jsonify
from flask_login import login_required, current_user
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from sqlalchemy import func
from extensions import db
from models import Event, EventParticipation, CastSignup, StatusAdjustment, Character, User
from datetime import datetime
import pytz

events_bp = Blueprint('events', __name__)

@events_bp.route('/events')
@login_required
def events():
    # Update event statuses immediately
    now = datetime.now()
    Event.query.filter(
        Event.status == 'Upcoming',
        Event.start_date <= now
    ).update({'status': 'In Progress'})
    Event.query.filter(
        Event.status == 'In Progress',
        Event.end_date <= now
    ).update({'status': 'Completed'})
    db.session.commit()
    upcoming_events = Event.query.filter_by(status='Upcoming').all()
    in_progress_events = Event.query.filter_by(status='In Progress').all()
    completed_events = Event.query.filter_by(status='Completed').all()
    # Filter to only events the user signed up for
    user_event_ids = set(
        [ep.event_id for ep in EventParticipation.query.filter_by(user_id=current_user.id).all()] +
        [cs.event_id for cs in CastSignup.query.filter_by(user_id=current_user.id).all()]
    )
    completed_events = [event for event in completed_events if event.id in user_event_ids]
    def get_event_counts(event):
        participant_count = db.session.query(EventParticipation.user_id)\
            .filter_by(event_id=event.id)\
            .distinct()\
            .count()
        cast_count = db.session.query(CastSignup.user_id)\
            .filter_by(event_id=event.id)\
            .distinct()\
            .count()
        return {
            'participant_count': participant_count,
            'cast_count': cast_count
        }
    upcoming_events = [(event, get_event_counts(event)) for event in upcoming_events]
    in_progress_events = [(event, get_event_counts(event)) for event in in_progress_events]
    completed_events = [(event, get_event_counts(event)) for event in completed_events]
    return render_template('events.html',
                         upcoming_events=upcoming_events,
                         in_progress_events=in_progress_events,
                         completed_events=completed_events)

@events_bp.route('/create_event', methods=['GET', 'POST'])
@login_required
def create_event():
    if request.method == 'POST':
        title = request.form.get('title')
        realm = request.form.get('realm')
        timeblocks = int(request.form.get('timeblocks'))
        start_date_str = request.form.get('start_date')
        end_date_str = request.form.get('end_date')
        start_date = datetime.fromisoformat(start_date_str)
        end_date = datetime.fromisoformat(end_date_str)
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
        return redirect(url_for('events.events'))
    return render_template('create_event.html')

@events_bp.route('/signup_event/<int:event_id>', methods=['GET', 'POST'])
@login_required
def signup_event(event_id):
    event = Event.query.get_or_404(event_id)
    if event.start_date <= datetime.now():
        flash('Cannot sign up for an event that has already started')
        return redirect(url_for('events.events'))
    if request.method == 'POST':
        signup_type = request.form.get('signup_type')
        if signup_type == 'participant':
            for timeblock in range(1, event.timeblocks + 1):
                character_id = request.form.get(f'character_{timeblock}')
                if character_id:
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
            character_id = request.form.get('cast_character')
            selected_timeblocks = request.form.getlist('cast_timeblocks')
            for timeblock in selected_timeblocks:
                timeblock = int(timeblock)
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
        return redirect(url_for('events.events'))
    realm_characters = Character.query.filter_by(
        user_id=current_user.id,
        realm=event.realm
    ).all()
    all_characters = Character.query.filter_by(
        user_id=current_user.id
    ).all()
    return render_template('event_signup.html', 
                         event=event, 
                         realm_characters=realm_characters,
                         all_characters=all_characters)

@events_bp.route('/event/<int:event_id>/roster')
@login_required
def event_roster(event_id):
    if not current_user.can_create_events:
        flash('You do not have permission to view this page')
        return redirect(url_for('events.events'))
    event = Event.query.get_or_404(event_id)
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
    return render_template('event_roster.html', event=event, participant_groups=participant_groups, cast_groups=cast_groups)

@events_bp.route('/event/<int:event_id>/roster_pdf')
@login_required
def event_roster_pdf(event_id):
    if not current_user.can_create_events:
        flash('You do not have permission to view this page')
        return redirect(url_for('events.events'))
    event = Event.query.get_or_404(event_id)
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
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    elements = []
    elements.append(Paragraph(f"Event Roster: {event.title}", styles['Title']))
    elements.append(Paragraph(f"Realm: {event.realm}", styles['Normal']))
    elements.append(Paragraph(f"Location: {event.location}", styles['Normal']))
    elements.append(Paragraph(f"Start: {event.start_date.strftime('%Y-%m-%d %I:%M %p')}", styles['Normal']))
    elements.append(Paragraph(f"End: {event.end_date.strftime('%Y-%m-%d %I:%M %p')}", styles['Normal']))
    total_participants = len(participant_groups)
    total_cast = len(cast_groups)
    elements.append(Paragraph(f"Total Participants: {total_participants}", styles['Heading3']))
    elements.append(Paragraph(f"Total Cast: {total_cast}", styles['Heading3']))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph("Participants", styles['Heading2']))
    if participant_groups:
        data = [["User", "Character", "Timeblocks"]]
        for group in participant_groups:
            data.append([
                f"{group['user'].first_name} {group['user'].last_name}",
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
    elements.append(Paragraph("Cast", styles['Heading2']))
    if cast_groups:
        data = [["User", "Character", "Timeblocks", "Status"]]
        for group in cast_groups:
            data.append([
                f"{group['user'].first_name} {group['user'].last_name}",
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

@events_bp.route('/status_management')
@login_required
def status_management():
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

@events_bp.route('/get_event_participants/<int:event_id>')
@login_required
def get_event_participants(event_id):
    event = Event.query.get_or_404(event_id)
    if event.processed:
        return redirect(url_for('events.status_management'))
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
        StatusAdjustment.id == None
    ).group_by(Character.id).order_by(Character.id).all()
    return render_template('event_participants.html',
                         event=event,
                         participants=participants)

@events_bp.route('/adjust_event_status', methods=['POST'])
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
        return redirect(url_for('events.status_management'))
    timeblock_count = EventParticipation.query.filter_by(
        event_id=event_id,
        character_id=character_id
    ).count()
    play_status = timeblock_count * 25
    total_status = (
        writing_status +
        management_status +
        service_status +
        cast_status +
        interaction_status +
        play_status
    )
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
                adjusted_by=current_user.id,
                event_id=event.id
            )
            db.session.add(adjustment)
    character.total_status += total_status
    character.status_remaining += total_status
    character.update_rank()
    remaining_participants = (
        db.session.query(EventParticipation.character_id)
        .filter_by(event_id=event_id)
        .distinct()
        .count()
    )
    pending_cast_signups = CastSignup.query.filter_by(
        event_id=event_id,
        status='Pending'
    ).count()
    if remaining_participants == 1 and pending_cast_signups == 0:
        event.processed = True
    db.session.commit()
    flash(f'Successfully added {total_status} status points to {character.name}')
    return redirect(url_for('events.get_event_participants', event_id=event_id))

@events_bp.route('/event/<int:event_id>/my_signups')
@login_required
def my_event_signups(event_id):
    event = Event.query.get_or_404(event_id)
    participant_signups = EventParticipation.query.filter_by(
        event_id=event_id,
        user_id=current_user.id
    ).all()
    cast_signups = CastSignup.query.filter_by(
        event_id=event_id,
        user_id=current_user.id
    ).all()
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
    all_signups.sort(key=lambda x: x['timeblock'])
    return render_template('my_event_signups.html',
                         event=event,
                         signups=all_signups)

@events_bp.route('/event_history')
@login_required
def event_history():
    if not (current_user.is_admin or current_user.is_moderator):
        flash('You do not have permission to access this page')
        return redirect(url_for('events.events'))
    sort_by = request.args.get('sort_by', 'date')
    order = request.args.get('order', 'desc')
    query = Event.query
    if sort_by == 'realm':
        query = query.order_by(Event.realm.asc() if order == 'asc' else Event.realm.desc())
    elif sort_by == 'title':
        query = query.order_by(Event.title.asc() if order == 'asc' else Event.title.desc())
    else:  # date
        query = query.order_by(Event.start_date.asc() if order == 'asc' else Event.start_date.desc())
    events = query.all()
    return render_template('event_history.html', events=events, sort_by=sort_by, order=order)

@events_bp.route('/event_history/<int:event_id>')
@login_required
def event_history_detail(event_id):
    if not (current_user.is_admin or current_user.is_moderator):
        flash('You do not have permission to access this page')
        return redirect(url_for('events.events'))
    event = Event.query.get_or_404(event_id)
    adjustments = StatusAdjustment.query.filter_by(event_id=event_id).all()
    # Group adjustments by user
    user_summaries = {}
    for adj in adjustments:
        user = User.query.get(adj.adjusted_by)
        character = Character.query.get(adj.character_id)
        if user.id not in user_summaries:
            user_summaries[user.id] = {
                'user': user,
                'characters': {}
            }
        if character.id not in user_summaries[user.id]['characters']:
            user_summaries[user.id]['characters'][character.id] = {
                'character': character,
                'adjustments': []
            }
        user_summaries[user.id]['characters'][character.id]['adjustments'].append(adj)
    # For backward compatibility, keep the old details list
    details = []
    for adj in adjustments:
        character = Character.query.get(adj.character_id)
        user = User.query.get(adj.adjusted_by)
        details.append({
            'character': character,
            'user': user,
            'amount': adj.amount,
            'status_type': adj.status_type,
            'notes': adj.notes,
            'date': adj.date
        })
    return render_template('event_history_detail.html', event=event, user_summaries=user_summaries, details=details)

@events_bp.route('/attended_events')
@login_required
def attended_events():
    # Collect participations and cast signups for the current user
    participations = EventParticipation.query.filter_by(user_id=current_user.id).all()
    cast_signups = CastSignup.query.filter_by(user_id=current_user.id).all()
    attended = {}
    # Group participations
    for p in participations:
        key = (p.event_id, p.character_id, 'Participant')
        if key not in attended:
            attended[key] = {'event': Event.query.get(p.event_id),
                             'character': Character.query.get(p.character_id),
                             'role': 'Participant',
                             'timeblocks': set(),
                             'status_gained': 0}
        attended[key]['timeblocks'].add(p.timeblock)
    # Group cast signups
    for c in cast_signups:
        key = (c.event_id, c.character_id, 'Cast')
        if key not in attended:
            attended[key] = {'event': Event.query.get(c.event_id),
                             'character': Character.query.get(c.character_id),
                             'role': 'Cast',
                             'timeblocks': set(),
                             'status_gained': 0}
        attended[key]['timeblocks'].add(c.timeblock)
    # Calculate status gained for each (event, character, role)
    for key, entry in attended.items():
        event_id, character_id, _ = key
        entry['status_gained'] = db.session.query(func.sum(StatusAdjustment.amount)).filter_by(event_id=event_id, character_id=character_id).scalar() or 0
        entry['timeblocks'] = sorted(entry['timeblocks'])
    # Convert to list and sort by event date descending
    attended_list = list(attended.values())
    attended_list.sort(key=lambda x: x['event'].start_date, reverse=True)
    return render_template('attended_events.html', attended=attended_list)

# ... (continue with the rest of the event-related routes and helpers) ... 