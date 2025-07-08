from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, login_user, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import User
from extensions import db
from datetime import datetime

main_routes_bp = Blueprint('main_routes', __name__)

@main_routes_bp.route('/')
def home():
    return render_template('home.html')

@main_routes_bp.route('/books')
def books():
    return render_template('books.html')

@main_routes_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        address = request.form.get('address')
        birthday_str = request.form.get('birthday')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        birthday = datetime.strptime(birthday_str, '%Y-%m-%d').date() if birthday_str else None
        if not birthday:
            flash('Birthday is required')
            return redirect(url_for('main_routes.register'))
        if password != confirm_password:
            flash('Passwords do not match')
            return redirect(url_for('main_routes.register'))
        if User.query.filter_by(email=email).first():
            flash('Email already registered')
            return redirect(url_for('main_routes.register'))
        user = User(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            address=address,
            birthday=birthday,
            password_hash=generate_password_hash(password),
            date_registered=datetime.utcnow()
        )
        db.session.add(user)
        db.session.commit()
        return redirect(url_for('main_routes.login'))
    return render_template('register.html')

@main_routes_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('users.my_characters'))
        flash('Invalid email or password')
    return render_template('login.html')

@main_routes_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main_routes.home'))

@main_routes_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = current_user
    if request.method == 'POST':
        user.first_name = request.form.get('first_name', user.first_name)
        user.last_name = request.form.get('last_name', user.last_name)
        user.phone = request.form.get('phone', user.phone)
        user.address = request.form.get('address', user.address)
        birthday_str = request.form.get('birthday')
        if birthday_str:
            user.birthday = datetime.strptime(birthday_str, '%Y-%m-%d').date()
        db.session.commit()
        flash('Profile updated successfully!')
        return redirect(url_for('main_routes.profile'))
    return render_template('profile.html', user=user) 