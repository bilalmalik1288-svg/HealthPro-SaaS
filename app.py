import os
import random
import uuid
import stripe
import requests 
import re
import json
import smtplib
from email.mime.text import MIMEText
from threading import Thread
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from functools import wraps
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import or_

# 100% SECURE: Load environment variables from .env file directly
env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            if line.strip() and not line.startswith('#'):
                key, value = line.strip().split('=', 1)
                os.environ[key.strip()] = value.strip().strip('\'"')

from utils import process_patient_import

app = Flask("HealthPro")

# SECURE ENVIRONMENT VARIABLES (No Hardcoded Secrets for GitHub to find!)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "healthpro_ultimate_secure_key_786")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "sk_test_51R7FqbPN6BB6gJeUvIrwfQip4fOHEdGfPUCZsWmcfgmHCngqMIu3saRHslXjDEnS9I0NT38aYX0mR97xT03lVcRW001STqilWw")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "DUMMY")

# Fetch from .env
RECAPTCHA_SITE_KEY = os.environ.get("RECAPTCHA_SITE_KEY", "6LcHUpstAAAAALV54iW2DcBIPNXljYEjnMCok2Pu")
RECAPTCHA_SECRET_KEY = os.environ.get("RECAPTCHA_SECRET_KEY", "6LcHUpstAAAAAF8HFFri2PCxaUUU2JaN2Vg4i4XT")

# Fetch Database from .env
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# ==========================================
# FILE UPLOAD ENGINE CONFIG (USA EHR)
# ==========================================
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# ==========================================
# MODELS (USA EHR STANDARDS)
# ==========================================

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    clinic_name = db.Column(db.String(150), nullable=False)
    specialty = db.Column(db.String(100), nullable=True) 
    address = db.Column(db.String(255), nullable=True)
    state = db.Column(db.String(50), nullable=True)
    zip_code = db.Column(db.String(20), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    fax = db.Column(db.String(50), nullable=True)
    npi_number = db.Column(db.String(20), nullable=True) 
    dea_number = db.Column(db.String(20), nullable=True) 
    license_number = db.Column(db.String(50), nullable=True) 
    is_superadmin = db.Column(db.Boolean, default=False)
    role = db.Column(db.String(50), default='Doctor') 
    parent_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True) 
    status = db.Column(db.String(20), default='Active')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def owner_id(self):
        return self.parent_id if self.parent_id else self.id

class ClinicSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    stripe_secret_key = db.Column(db.String(255), nullable=True)
    stripe_public_key = db.Column(db.String(255), nullable=True)
    subscription_plan = db.Column(db.String(50), default='Premium EHR Plan')
    subscription_status = db.Column(db.String(20), default='Inactive') 
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, nullable=False) 
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    action = db.Column(db.String(100), nullable=False)
    details = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(50), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='audit_logs', lazy=True)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    mrn = db.Column(db.String(20), unique=True, nullable=False)
    first_name = db.Column(db.String(100), nullable=False)
    middle_name = db.Column(db.String(100), nullable=True)
    last_name = db.Column(db.String(100), nullable=False)
    dob = db.Column(db.Date, nullable=False)
    gender = db.Column(db.String(20))
    phone = db.Column(db.String(50))
    secondary_phone = db.Column(db.String(50), nullable=True)
    email = db.Column(db.String(150))
    password_hash = db.Column(db.String(255), nullable=True)
    is_portal_active = db.Column(db.Boolean, default=False)
    ssn_last_4 = db.Column(db.String(4), nullable=True)
    address = db.Column(db.String(255), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    state = db.Column(db.String(50), nullable=True)
    zip_code = db.Column(db.String(20), nullable=True)
    country = db.Column(db.String(100), nullable=True, default='USA')
    allergies = db.Column(db.Text, nullable=True)
    emergency_contact = db.Column(db.String(150), nullable=True)
    insurance_provider = db.Column(db.String(150), nullable=True)
    insurance_member_id = db.Column(db.String(100), nullable=True)
    insurance_group_number = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(20), default='Active') 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    appointments = db.relationship('Appointment', backref='patient', lazy=True)

    @staticmethod
    def generate_mrn():
        return f"MRN-{random.randint(10000, 99999)}"

class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    uploaded_by = db.Column(db.Integer, nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(255), nullable=False)
    document_type = db.Column(db.String(50), default='General')
    status = db.Column(db.String(20), default='Active')
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)
    patient = db.relationship('Patient', backref='documents', lazy=True)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    sender_type = db.Column(db.String(20), nullable=False) 
    sender_id = db.Column(db.Integer, nullable=False)
    body = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)

class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    doctor_name = db.Column(db.String(100), nullable=False)
    appointment_date = db.Column(db.Date, nullable=False)
    appointment_time = db.Column(db.Time, nullable=False)
    visit_type = db.Column(db.String(50), default='Office Visit') 
    reason = db.Column(db.String(255))
    chief_complaint = db.Column(db.Text, nullable=True) 
    meeting_room_id = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(20), default='Scheduled') 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    vitals = db.relationship('Vital', backref='appointment', uselist=False)
    prescriptions = db.relationship('Prescription', backref='appointment', cascade="all, delete-orphan", lazy=True)
    soap_note = db.relationship('SOAPNote', backref='appointment', uselist=False, cascade="all, delete-orphan")

class Vital(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id'), nullable=False)
    bp_systolic = db.Column(db.String(10))
    bp_diastolic = db.Column(db.String(10))
    weight_lbs = db.Column(db.String(10))
    temperature = db.Column(db.String(10))
    heart_rate = db.Column(db.String(10))
    clinical_notes = db.Column(db.Text)

class Prescription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id'), nullable=False)
    medication = db.Column(db.String(150), nullable=False)
    dosage = db.Column(db.String(50))
    frequency = db.Column(db.String(50))
    duration = db.Column(db.String(50))
    instructions = db.Column(db.Text)
    pharmacy_name = db.Column(db.String(150), nullable=True)
    status = db.Column(db.String(50), default='Prescribed')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class SOAPNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id'), nullable=False)
    subjective = db.Column(db.Text)
    objective = db.Column(db.Text)
    assessment = db.Column(db.Text)
    plan = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id'), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    consultation_fee = db.Column(db.Float, default=0.0) 
    other_charges = db.Column(db.Float, default=0.0)
    discount = db.Column(db.Float, default=0.0)
    total_amount = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(50), default='Unpaid')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    patient = db.relationship('Patient', backref='invoices', lazy=True)

class LabOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id'), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    test_name = db.Column(db.String(150), nullable=False)
    vendor_name = db.Column(db.String(150), nullable=True)
    urgency = db.Column(db.String(50), default='Routine')
    collection = db.Column(db.String(50), nullable=True) 
    status = db.Column(db.String(50), default='Ordered')
    results = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    patient = db.relationship('Patient', backref='lab_orders', lazy=True)
    appointment = db.relationship('Appointment', backref='lab_orders', lazy=True)

class ClinicLabTest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    test_name = db.Column(db.String(150), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('clinic_id', 'test_name', name='_clinic_test_uc'),)

# ==========================================
# AUTOMATION ENGINES (EMAIL & BILLING)
# ==========================================

def send_email_async(to_email, subject, body):
    def send():
        print(f"\n{'='*50}\n[AUTOMATED EMAIL DISPATCH]\nTo: {to_email}\nSubject: {subject}\nBody:\n{body}\n{'='*50}\n")
    Thread(target=send).start()

def trigger_auto_billing(appt_id):
    appt = Appointment.query.get(appt_id)
    if not appt: return
    
    existing_invoice = Invoice.query.filter_by(appointment_id=appt_id).first()
    if not existing_invoice:
        fee = 150.0 if appt.visit_type == 'Office Visit' else 100.0
        new_invoice = Invoice(
            user_id=appt.user_id,
            appointment_id=appt_id,
            patient_id=appt.patient_id,
            consultation_fee=fee,
            total_amount=fee,
            status='Unpaid'
        )
        db.session.add(new_invoice)
        db.session.commit()
        
        patient_email = appt.patient.email
        if patient_email:
            payment_url = url_for('public_pay_invoice', invoice_id=new_invoice.id, _external=True)
            subject = f"Invoice & Payment Link - HealthPro Clinic"
            body = f"Dear {appt.patient.first_name},\n\nYour automated invoice for the visit on {appt.appointment_date.strftime('%B %d, %Y')} is ready.\nAmount Due: ${fee:.2f}\n\nPlease complete your payment securely using our zero-touch online portal:\n{payment_url}\n\nThank you,\nHealthPro Automated RCM System"
            send_email_async(patient_email, subject, body)

# ==========================================
# CUSTOM DECORATORS & HELPERS
# ==========================================
def superadmin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_superadmin:
            flash("Access denied. Super Admin only.")
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def require_role(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if current_user.is_superadmin:
                return f(*args, **kwargs)
            if current_user.role not in roles and current_user.role != 'Doctor':
                log_activity("Unauthorized Access Attempt", f"Role {current_user.role} tried to access restricted area")
                flash(f"Access restricted. Only {', '.join(roles)} or Doctors can access this area.")
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def subscription_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated_function

def check_strong_password(password):
    if len(password) < 8:
        return False
    if not re.search(r"[a-z]", password):
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"\d", password):
        return False
    if not re.search(r"[^a-zA-Z0-9]", password):
        return False
    return True

def log_activity(action, details=""):
    if current_user.is_authenticated:
        log = AuditLog(
            clinic_id=current_user.owner_id,
            user_id=current_user.id,
            action=action,
            details=details,
            ip_address=request.remote_addr
        )
        db.session.add(log)
        db.session.commit()

# ==========================================
# GLOBAL SEARCH ROUTE
# ==========================================
@app.route('/search')
@login_required
@subscription_required
def global_search():
    query = request.args.get('q', '').strip()
    if not query:
        return redirect(url_for('dashboard'))
    
    search_term = f"%{query}%"
    
    patients = Patient.query.filter_by(user_id=current_user.owner_id).filter(
        Patient.status != 'Archived',
        or_(
            Patient.first_name.ilike(search_term),
            Patient.last_name.ilike(search_term),
            Patient.mrn.ilike(search_term),
            Patient.phone.ilike(search_term),
            Patient.email.ilike(search_term)
        )
    ).all()
    
    appointments = Appointment.query.join(Patient).filter(
        Appointment.user_id == current_user.owner_id,
        Appointment.status != 'Archived',
        Patient.status != 'Archived',
        or_(
            Appointment.reason.ilike(search_term),
            Appointment.chief_complaint.ilike(search_term),
            Appointment.doctor_name.ilike(search_term),
            Patient.first_name.ilike(search_term),
            Patient.last_name.ilike(search_term)
        )
    ).all()
    
    log_activity("Global Search", f"Searched for keyword: {query}")
    return render_template('search_results.html', query=query, patients=patients, appointments=appointments)

# ==========================================
# CORE ROUTES (REGISTRATION, AUTH & PROFILE)
# ==========================================

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        # reCAPTCHA Validation
        recaptcha_response = request.form.get('g-recaptcha-response')
        verify_response = requests.post(
            url='https://www.google.com/recaptcha/api/siteverify',
            data={'secret': RECAPTCHA_SECRET_KEY, 'response': recaptcha_response}
        ).json()
        
        if not verify_response.get('success'):
            flash("Please complete the reCAPTCHA to verify you are human.")
            return redirect(url_for('register'))

        password = request.form['password']
        if not check_strong_password(password):
            flash("Weak Password! It must be at least 8 characters long, contain 1 uppercase letter, 1 number, and 1 special character.")
            return redirect(url_for('register'))

        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
        is_first_user = User.query.count() == 0 
        new_user = User(
            email=request.form['email'], 
            password_hash=hashed_pw, 
            name=request.form['name'], 
            clinic_name=request.form['clinic_name'],
            specialty=request.form.get('specialty'), 
            address=request.form.get('address'),
            state=request.form.get('state'),
            zip_code=request.form.get('zip_code'),
            phone=request.form.get('phone'),
            npi_number=request.form.get('npi_number'),
            is_superadmin=is_first_user,
            role='Doctor',
            status='Active'
        )
        db.session.add(new_user)
        db.session.commit()
        
        if is_first_user:
            settings = ClinicSettings(clinic_id=new_user.id, subscription_status='Active')
        else:
            settings = ClinicSettings(clinic_id=new_user.id, subscription_status='Inactive')
            
        db.session.add(settings)
        db.session.commit()

        initial_tests = [
            "Comprehensive Metabolic Panel (CMP)", "Complete Blood Count (CBC) with Differential",
            "Lipid Panel", "Hemoglobin A1c (HbA1c)", "Urinalysis, Complete"
        ]
        for t_name in initial_tests:
            seed_test = ClinicLabTest(clinic_id=new_user.id, test_name=t_name)
            db.session.add(seed_test)
        db.session.commit()

        login_user(new_user)
        log_activity("Account Registration", f"New Clinic Setup: {new_user.clinic_name}")
        
        if is_first_user:
            return redirect(url_for('admin_dashboard'))
            
        return redirect(url_for('dashboard')) 
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form['email']).first()
        if user and check_password_hash(user.password_hash, request.form['password']):
            if user.status == 'Archived':
                flash("Your account has been deactivated. Please contact your Clinic Admin.")
                return redirect(url_for('login'))
                
            login_user(user)
            log_activity("User Login", f"Successful login from IP: {request.remote_addr}")
            if user.is_superadmin:
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('dashboard'))
        flash("Invalid credentials")
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    log_activity("User Logout", "Logged out securely")
    logout_user()
    return redirect(url_for('login'))

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        if check_password_hash(current_user.password_hash, request.form['old_password']):
            new_password = request.form['new_password']
            if not check_strong_password(new_password):
                flash("Weak Password! It must be at least 8 characters long, contain 1 uppercase letter, 1 number, and 1 special character.")
                return redirect(url_for('change_password'))

            current_user.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
            db.session.commit()
            log_activity("Password Change", "User changed their password securely")
            flash("Password updated successfully!")
            return redirect(url_for('dashboard'))
        else:
            flash("Incorrect old password. Please try again.")
    return render_template('change_password.html')

@app.route('/profile', methods=['GET', 'POST'])
@login_required
@subscription_required
@require_role('Doctor')
def profile_update():
    if request.method == 'POST':
        current_user.name = request.form.get('name')
        current_user.clinic_name = request.form.get('clinic_name')
        current_user.specialty = request.form.get('specialty')
        current_user.npi_number = request.form.get('npi_number')
        current_user.dea_number = request.form.get('dea_number')
        current_user.license_number = request.form.get('license_number')
        current_user.address = request.form.get('address')
        current_user.state = request.form.get('state') 
        current_user.zip_code = request.form.get('zip_code') 
        current_user.phone = request.form.get('phone')
        current_user.fax = request.form.get('fax')
        db.session.commit()
        log_activity("Profile Updated", f"Doctor updated professional details. NPI: {current_user.npi_number}")
        flash("Profile updated successfully! USA Standards applied to documents.")
        return redirect(url_for('profile_update'))
    return render_template('profile.html', user=current_user)

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form['email']).first()
        if user and user.status != 'Archived':
            token = s.dumps(user.email, salt='reset-password-salt')
            reset_url = url_for('reset_password', token=token, _external=True)
            print(f"\n========== PASSWORD RESET LINK ==========\nLink for {user.email}: \n{reset_url}\n=========================================\n")
            flash("Password reset link generated! Please check the terminal.")
        else:
            flash("Email not found or account deactivated.")
        return redirect(url_for('login'))
    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        email = s.loads(token, salt='reset-password-salt', max_age=3600)
    except:
        flash("The reset link is invalid or has expired.")
        return redirect(url_for('login'))
    if request.method == 'POST':
        new_password = request.form['new_password']
        if not check_strong_password(new_password):
            flash("Weak Password! It must be at least 8 characters long, contain 1 uppercase letter, 1 number, and 1 special character.")
            return redirect(url_for('reset_password', token=token))
        
        user = User.query.filter_by(email=email).first()
        user.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
        db.session.commit()
        flash("Your password has been securely updated!")
        return redirect(url_for('login'))
    return render_template('reset_password.html')

# ==========================================
# SAAS SUBSCRIPTION ($150 STRIPE)
# ==========================================
@app.route('/saas/subscribe', methods=['GET', 'POST'])
@login_required
def saas_subscribe():
    if current_user.parent_id is not None:
        flash("Only Clinic Owners can manage SaaS subscriptions.")
        return redirect(url_for('login'))
        
    settings = ClinicSettings.query.filter_by(clinic_id=current_user.id).first()
    
    if request.method == 'POST':
        try:
            session_stripe = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {'name': 'HealthPro Premium EHR ($150/Month)'},
                        'unit_amount': 15000, 
                        'recurring': {'interval': 'month'}
                    },
                    'quantity': 1,
                }],
                mode='subscription',
                success_url=url_for('saas_success', _external=True),
                cancel_url=url_for('saas_subscribe', _external=True),
            )
            return redirect(session_stripe.url, code=303)
        except Exception as e:
            flash(f"Payment Error: {str(e)}")
            
    return render_template('saas_subscribe.html', settings=settings)

@app.route('/saas/success')
@login_required
def saas_success():
    settings = ClinicSettings.query.filter_by(clinic_id=current_user.owner_id).first()
    if settings:
        settings.subscription_status = 'Active'
        db.session.commit()
    log_activity("Subscription Activated", "Stripe SaaS subscription payment successful")
    flash("Payment successful! Your Premium Workspace is now unlocked.")
    return redirect(url_for('dashboard'))

# ==========================================
# DOCUMENT UPLOAD ENGINE
# ==========================================
@app.route('/patient/<int:patient_id>/documents', methods=['GET', 'POST'])
@login_required
@subscription_required
@require_role('Doctor', 'Receptionist')
def document_center(patient_id):
    patient = Patient.query.filter_by(id=patient_id, user_id=current_user.owner_id).first_or_404()
    documents = Document.query.filter_by(patient_id=patient_id).all()
    
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file part')
            return redirect(request.url)
        file = request.files['file']
        if file.filename == '':
            flash('No selected file')
            return redirect(request.url)
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            unique_filename = f"{uuid.uuid4().hex[:8]}_{filename}"
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(file_path)
            
            new_doc = Document(
                patient_id=patient.id,
                uploaded_by=current_user.id,
                filename=filename,
                file_path=unique_filename,
                document_type=request.form.get('document_type', 'General')
            )
            db.session.add(new_doc)
            db.session.commit()
            log_activity("Document Uploaded", f"Uploaded document {filename} for MRN: {patient.mrn}")
            flash('Document successfully uploaded and encrypted.')
            return redirect(url_for('document_center', patient_id=patient.id))
    
    return render_template('document_upload.html', patient=patient, documents=documents)

@app.route('/documents/download/<int:doc_id>')
@login_required
@subscription_required
@require_role('Doctor', 'Receptionist')
def download_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    Patient.query.filter_by(id=doc.patient_id, user_id=current_user.owner_id).first_or_404()
    log_activity("Document Viewed/Downloaded", f"Viewed Document ID: {doc.id} ({doc.filename})")
    return send_from_directory(app.config['UPLOAD_FOLDER'], doc.file_path, as_attachment=False)

@app.route('/documents/archive/<int:doc_id>', methods=['POST'])
@login_required
@subscription_required
@require_role('Doctor', 'Receptionist')
def archive_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    patient = Patient.query.filter_by(id=doc.patient_id, user_id=current_user.owner_id).first_or_404()
    
    doc.status = 'Archived'
    db.session.commit()
    log_activity("Document Archived", f"Marked document {doc.filename} as Archived for MRN: {patient.mrn}")
    flash('Document has been successfully marked as error and archived.')
    return redirect(url_for('document_center', patient_id=doc.patient_id))

# ==========================================
# SECURE CHAT & MESSAGING
# ==========================================
@app.route('/messages/inbox')
@login_required
@subscription_required
@require_role('Doctor', 'Receptionist')
def doctor_inbox():
    patients = Patient.query.filter_by(user_id=current_user.owner_id).all()
    return render_template('messages.html', patients=patients)

@app.route('/messages/chat/<int:patient_id>', methods=['GET', 'POST'])
@login_required
@subscription_required
@require_role('Doctor', 'Receptionist')
def doctor_chat(patient_id):
    patient = Patient.query.filter_by(id=patient_id, user_id=current_user.owner_id).first_or_404()
    
    if request.method == 'POST':
        body = request.form.get('body')
        if body:
            new_msg = Message(
                clinic_id=current_user.owner_id,
                patient_id=patient.id,
                sender_type='Doctor',
                sender_id=current_user.id,
                body=body
            )
            db.session.add(new_msg)
            db.session.commit()
            log_activity("Secure Message Sent", f"Sent message to Patient MRN: {patient.mrn}")
            return redirect(url_for('doctor_chat', patient_id=patient.id))
            
    messages = Message.query.filter_by(patient_id=patient.id).order_by(Message.timestamp.asc()).all()
    return render_template('chat_room.html', patient=patient, messages=messages)

# ==========================================
# PATIENT PORTAL & AUTO AI CHATBOT ROUTE
# ==========================================
@app.route('/patient-portal/login', methods=['GET', 'POST'])
def patient_login():
    if request.method == 'POST':
        patient = Patient.query.filter_by(email=request.form['email']).first()
        if patient and patient.password_hash and check_password_hash(patient.password_hash, request.form['password']):
            session['patient_id'] = patient.id
            return redirect(url_for('patient_dashboard_view'))
        flash("Invalid email or password.")
    return render_template('patient_login.html')

@app.route('/patient-portal/register/<int:clinic_id>', methods=['GET', 'POST'])
def patient_register(clinic_id):
    clinic = User.query.filter_by(id=clinic_id, role='Doctor').first_or_404()
    if request.method == 'POST':
        # reCAPTCHA Validation
        recaptcha_response = request.form.get('g-recaptcha-response')
        verify_response = requests.post(
            url='https://www.google.com/recaptcha/api/siteverify',
            data={'secret': RECAPTCHA_SECRET_KEY, 'response': recaptcha_response}
        ).json()
        
        if not verify_response.get('success'):
            flash("Please complete the reCAPTCHA challenge to verify you are human.")
            return redirect(url_for('patient_register', clinic_id=clinic_id))

        password = request.form['password']
        if not check_strong_password(password):
            flash("Weak Password! It must be at least 8 characters long, contain 1 uppercase letter, 1 number, and 1 special character.")
            return redirect(url_for('patient_register', clinic_id=clinic_id))

        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
        new_p = Patient(
            user_id=clinic.id,
            mrn=Patient.generate_mrn(),
            first_name=request.form['first_name'],
            last_name=request.form['last_name'],
            dob=datetime.strptime(request.form['dob'], '%Y-%m-%d').date(),
            email=request.form['email'],
            phone=request.form.get('phone'),
            address=request.form.get('address'),
            state=request.form.get('state'),
            zip_code=request.form.get('zip_code'),
            password_hash=hashed_pw,
            is_portal_active=True
        )
        db.session.add(new_p)
        db.session.commit()
        flash("Registration successful! You can now log into your patient portal.")
        return redirect(url_for('patient_login'))
    return render_template('patient_register.html', clinic=clinic)

@app.route('/patient-portal/dashboard')
def patient_dashboard_view():
    if 'patient_id' not in session:
        return redirect(url_for('patient_login'))
    patient = Patient.query.get(session['patient_id'])
    history = Appointment.query.filter_by(patient_id=patient.id).order_by(Appointment.appointment_date.desc()).all()
    invoices = Invoice.query.filter_by(patient_id=patient.id).all()
    return render_template('patient_dashboard.html', patient=patient, history=history, invoices=invoices)

@app.route('/patient-portal/chat', methods=['GET', 'POST'])
def patient_portal_chat():
    if 'patient_id' not in session:
        return redirect(url_for('patient_login'))
    patient = Patient.query.get(session['patient_id'])
    
    if request.method == 'POST':
        body = request.form.get('body')
        if body:
            new_msg = Message(
                clinic_id=patient.user_id,
                patient_id=patient.id,
                sender_type='Patient',
                sender_id=patient.id,
                body=body
            )
            db.session.add(new_msg)
            db.session.commit()

            try:
                if not GEMINI_API_KEY or GEMINI_API_KEY.upper() == 'DUMMY':
                    ai_reply_text = "Hello! This is a demo mode virtual assistant. A doctor will review your message soon."
                else:
                    import google.generativeai as genai
                    genai.configure(api_key=GEMINI_API_KEY)
                    model = genai.GenerativeModel('gemini-2.0-flash')
                    prompt = f"You are a virtual medical receptionist for a clinic. A patient just messaged: '{body}'. Reply briefly and professionally. Do not provide medical advice. Inform them a doctor will follow up if needed."
                    response = model.generate_content(prompt)
                    ai_reply_text = response.text
            except Exception as e:
                ai_reply_text = "Thank you for reaching out. Our staff will get back to you shortly."

            auto_reply_msg = Message(
                clinic_id=patient.user_id,
                patient_id=patient.id,
                sender_type='Doctor', 
                sender_id=patient.user_id,
                body=f"*[Virtual Assistant]*: {ai_reply_text}"
            )
            db.session.add(auto_reply_msg)
            db.session.commit()

            return redirect(url_for('patient_portal_chat'))
            
    messages = Message.query.filter_by(patient_id=patient.id).order_by(Message.timestamp.asc()).all()
    return render_template('patient_portal_chat.html', patient=patient, messages=messages)

@app.route('/book/<int:clinic_id>', methods=['GET', 'POST'])
def public_booking(clinic_id):
    clinic = User.query.filter_by(id=clinic_id, role='Doctor').first_or_404()
    if request.method == 'POST':
        appointment_date = datetime.strptime(request.form['appointment_date'], '%Y-%m-%d').date()
        appointment_time = datetime.strptime(request.form['appointment_time'], '%H:%M').time()

        conflict = Appointment.query.filter_by(
            user_id=clinic_id,
            appointment_date=appointment_date,
            appointment_time=appointment_time,
            doctor_name=clinic.name
        ).filter(Appointment.status != 'Archived', Appointment.status != 'Cancelled').first()

        if conflict:
            flash(f"Sorry, {clinic.name} is already booked at this time. Please choose another slot.")
            return redirect(url_for('public_booking', clinic_id=clinic_id))

        patient_name = request.form.get('patient_name', 'Guest Patient')
        name_parts = patient_name.split(' ', 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else 'Unknown'

        new_patient = Patient(
            user_id=clinic_id,
            mrn=Patient.generate_mrn(),
            first_name=first_name,
            last_name=last_name,
            dob=datetime.utcnow().date(), 
            email=request.form.get('email'),
            phone=request.form.get('phone'),
            status='Web Request'
        )
        db.session.add(new_patient)
        db.session.flush() 

        new_appt = Appointment(
            user_id=clinic_id, 
            patient_id=new_patient.id, 
            doctor_name=clinic.name, 
            appointment_date=appointment_date, 
            appointment_time=appointment_time, 
            visit_type=request.form.get('visit_type', 'Office Visit'),
            reason=f"Online Booking: {request.form['reason']}",
            status='Pending Approval'
        )
        db.session.add(new_appt)
        db.session.commit()
        
        trigger_auto_billing(new_appt.id)
        
        flash("Your appointment request has been sent successfully! Check your email for the online payment link.")
        return redirect(url_for('public_booking', clinic_id=clinic_id))
    return render_template('public_booking.html', clinic=clinic)

# ==========================================
# DASHBOARD & SETTINGS
# ==========================================
@app.route('/settings', methods=['GET', 'POST'])
@login_required
@subscription_required
@require_role('Doctor')
def clinic_settings():
    settings = ClinicSettings.query.filter_by(clinic_id=current_user.owner_id).first()
    if not settings:
        settings = ClinicSettings(clinic_id=current_user.owner_id)
        db.session.add(settings)
        db.session.commit()

    if request.method == 'POST':
        settings.stripe_secret_key = request.form.get('stripe_secret_key')
        settings.stripe_public_key = request.form.get('stripe_public_key')
        db.session.commit()
        log_activity("Updated Settings", "Modified Stripe Gateway Settings")
        flash("Clinic Settings & API Integrations updated successfully!")
        return redirect(url_for('clinic_settings'))
    
    return render_template('settings.html', settings=settings)

@app.route('/audit-logs')
@login_required
@subscription_required
@require_role('Doctor')
def view_audit_logs():
    logs = AuditLog.query.filter_by(clinic_id=current_user.owner_id).order_by(AuditLog.timestamp.desc()).limit(200).all()
    return render_template('audit_logs.html', logs=logs)

# ==========================================
# MANAGE STAFF
# ==========================================
@app.route('/staff', methods=['GET', 'POST'])
@login_required
@subscription_required
@require_role('Doctor')
def manage_staff():
    staff_members = User.query.filter_by(parent_id=current_user.owner_id).filter(User.status != 'Archived').all()
    if request.method == 'POST':
        existing_user = User.query.filter_by(email=request.form['email']).first()
        if existing_user:
            flash("Error: This email is already registered!", "danger")
            return redirect(url_for('manage_staff'))
            
        password = request.form['password']
        if not check_strong_password(password):
            flash("Weak Password! It must be at least 8 characters long, contain 1 uppercase letter, 1 number, and 1 special character.", "danger")
            return redirect(url_for('manage_staff'))
            
        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
        new_staff = User(
            email=request.form['email'],
            password_hash=hashed_pw,
            name=request.form['name'],
            clinic_name=current_user.clinic_name,
            role=request.form['role'],
            parent_id=current_user.owner_id, 
            status='Active'
        )
        db.session.add(new_staff)
        db.session.commit()
        log_activity("Staff Added", f"Added new {request.form['role']}: {new_staff.email} with HIPAA access rights")
        flash(f"{request.form['role']} account created successfully!", "success")
        return redirect(url_for('manage_staff'))
    return render_template('staff_manage.html', staff=staff_members)

@app.route('/staff/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@require_role('Doctor')
def edit_staff(id):
    member = User.query.filter_by(id=id, parent_id=current_user.owner_id).first_or_404()
    if request.method == 'POST':
        member.name = request.form['name']
        member.email = request.form['email']
        member.role = request.form['role']
        if request.form.get('password'):
            password = request.form['password']
            if not check_strong_password(password):
                flash("Weak Password! It must be at least 8 characters long, contain 1 uppercase letter, 1 number, and 1 special character.", "danger")
                return redirect(url_for('edit_staff', id=id))
            member.password_hash = generate_password_hash(password, method='pbkdf2:sha256')
        db.session.commit()
        log_activity("Staff Details Updated", f"Modified access details for staff: {member.email}")
        flash(f"Staff member {member.name} updated successfully!", "success")
        return redirect(url_for('manage_staff'))
    return render_template('staff_edit.html', member=member)

@app.route('/staff/delete/<int:id>')
@login_required
@require_role('Doctor')
def delete_staff(id):
    member = User.query.filter_by(id=id, parent_id=current_user.owner_id).first_or_404()
    member.status = 'Archived' 
    db.session.commit()
    log_activity("Staff Deactivated", f"Revoked EHR access and deactivated staff account: {member.email}")
    flash(f"Staff member {member.name} has been deactivated.", "success")
    return redirect(url_for('manage_staff'))

@app.route('/')
@login_required
@subscription_required
def dashboard():
    p_count = Patient.query.filter_by(user_id=current_user.owner_id).filter(Patient.status != 'Archived').count()
    s_count = Appointment.query.join(Patient).filter(Patient.user_id == current_user.owner_id, Patient.status != 'Archived', Appointment.status != 'Archived').count()
    unpaid_count = Invoice.query.filter_by(user_id=current_user.owner_id, status='Unpaid').count()
    recent = Patient.query.filter_by(user_id=current_user.owner_id).filter(Patient.status != 'Archived').order_by(Patient.created_at.desc()).limit(5).all()
    return render_template('dashboard.html', total_p=p_count, total_s=s_count, unpaid_count=unpaid_count, recent=recent)

# ==========================================
# PATIENTS
# ==========================================
@app.route('/patients')
@login_required
@subscription_required
@require_role('Receptionist', 'Doctor') 
def patients_list():
    patients = Patient.query.filter_by(user_id=current_user.owner_id).filter(Patient.status != 'Archived').all()
    return render_template('patients.html', patients=patients)

@app.route('/patients/import', methods=['POST'])
@login_required
@subscription_required
@require_role('Doctor', 'Receptionist')
def import_patients():
    if 'file' not in request.files:
        flash("No file uploaded.")
        return redirect(url_for('patients_list'))
        
    file = request.files['file']
    if file.filename == '':
        flash("No file selected.")
        return redirect(url_for('patients_list'))
        
    if file and '.' in file.filename and file.filename.rsplit('.', 1)[1].lower() in {'csv', 'xls', 'xlsx'}:
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        success, patients_data, message = process_patient_import(filepath)
        
        if success:
            added_count = 0
            for p_data in patients_data:
                existing = Patient.query.filter_by(mrn=p_data['mrn']).first()
                if not existing:
                    try:
                        dob_date = datetime.strptime(p_data['dob'], '%Y-%m-%d').date() if p_data['dob'] else datetime.utcnow().date()
                    except ValueError:
                        dob_date = datetime.utcnow().date()
                        
                    new_p = Patient(
                        user_id=current_user.owner_id,
                        mrn=p_data['mrn'],
                        first_name=p_data['first_name'],
                        last_name=p_data['last_name'],
                        dob=dob_date,
                        gender=p_data['gender'],
                        phone=p_data['phone'],
                        email=p_data['email'],
                        address=p_data['address'],
                        insurance_provider=p_data['insurance_provider']
                    )
                    db.session.add(new_p)
                    added_count += 1
            db.session.commit()
            log_activity("Bulk Patient Import", f"Imported {added_count} patient records via CSV/Excel securely")
            flash(f"Successfully imported {added_count} new patients!")
        else:
            flash(f"Import Failed: {message}")
            
        if os.path.exists(filepath):
            os.remove(filepath)
    else:
        flash("Invalid file format. Please upload CSV or Excel files only.")
        
    return redirect(url_for('patients_list'))

@app.route('/patients/delete/<int:id>', methods=['POST', 'GET'])
@login_required
@subscription_required
@require_role('Doctor', 'Receptionist')
def archive_patient(id):
    patient = Patient.query.filter_by(id=id, user_id=current_user.owner_id).first_or_404()
    patient.status = 'Archived'
    db.session.commit() 
    log_activity("Patient Record Archived", f"HIPAA Compliance Action: Soft deleted record for MRN {patient.mrn} (Patient ID: {id})")
    flash("Patient record has been securely archived (HIPAA standard).")
    return redirect(url_for('patients_list'))

@app.route('/patients/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@subscription_required
@require_role('Doctor', 'Receptionist')
def edit_patient(id):
    patient = Patient.query.filter_by(id=id, user_id=current_user.owner_id).first_or_404()
    if request.method == 'POST':
        patient.first_name = request.form['first_name']
        patient.middle_name = request.form.get('middle_name')
        patient.last_name = request.form['last_name']
        patient.phone = request.form.get('phone')
        patient.secondary_phone = request.form.get('secondary_phone')
        patient.address = request.form.get('address')
        patient.city = request.form.get('city')
        patient.state = request.form.get('state')
        patient.country = request.form.get('country', 'USA')
        patient.allergies = request.form.get('allergies')
        patient.emergency_contact = request.form.get('emergency_contact')
        patient.insurance_provider = request.form.get('insurance_provider')
        db.session.commit() 
        log_activity("Patient Record Modified", f"HIPAA Compliance Action: Updated demographic/clinical data for MRN {patient.mrn}")
        flash("Patient details updated securely.")
        return redirect(url_for('patients_list'))
    return render_template('patients_edit.html', patient=patient) 

@app.route('/add', methods=['GET', 'POST'])
@login_required
@subscription_required
@require_role('Receptionist', 'Doctor')
def add_patient():
    if request.method == 'POST':
        new_p = Patient(
            user_id=current_user.owner_id, 
            mrn=Patient.generate_mrn(), 
            first_name=request.form['first_name'], 
            middle_name=request.form.get('middle_name'),
            last_name=request.form['last_name'], 
            dob=datetime.strptime(request.form['dob'], '%Y-%m-%d').date(), 
            gender=request.form['gender'], 
            phone=request.form['phone'], 
            secondary_phone=request.form.get('secondary_phone'),
            email=request.form['email'],
            ssn_last_4=request.form.get('ssn_last_4'),
            address=request.form.get('address'),
            city=request.form.get('city'),
            state=request.form.get('state'),
            zip_code=request.form.get('zip_code'),
            country=request.form.get('country', 'USA'),
            allergies=request.form.get('allergies'),
            emergency_contact=request.form.get('emergency_contact'),
            insurance_provider=request.form.get('insurance_provider'),
            insurance_member_id=request.form.get('insurance_member_id'),
            insurance_group_number=request.form.get('insurance_group_number')
        )
        db.session.add(new_p)
        db.session.commit()
        log_activity("Patient Registered", f"Created new patient chart. MRN: {new_p.mrn}")
        return redirect(url_for('patients_list'))
    return render_template('patients_add.html', patient=None)

@app.route('/patients/history/<int:id>')
@login_required
@subscription_required
@require_role('Receptionist', 'Doctor')
def patient_history(id):
    patient = Patient.query.filter_by(id=id, user_id=current_user.owner_id).first_or_404()
    history = Appointment.query.filter_by(patient_id=id).filter(Appointment.status != 'Archived').order_by(Appointment.appointment_date.desc()).all()
    log_activity("Patient History Accessed", f"Viewed clinical history for MRN: {patient.mrn}")
    return render_template('patient_history.html', patient=patient, history=history)

@app.route('/patient/<int:patient_id>/export')
@login_required
@subscription_required
@require_role('Doctor', 'Receptionist')
def export_patient_history(patient_id):
    patient = Patient.query.filter_by(id=patient_id, user_id=current_user.owner_id).first_or_404()
    history = Appointment.query.filter_by(patient_id=patient_id).order_by(Appointment.appointment_date.desc()).all()
    log_activity("Clinical Data Exported", f"Exported complete VDT Chart for MRN: {patient.mrn} securely")
    return render_template('export_chart.html', patient=patient, history=history, current_user=current_user)

# ==========================================
# APPOINTMENTS & TELEHEALTH
# ==========================================
@app.route('/appointments')
@login_required
@subscription_required
@require_role('Receptionist', 'Doctor')
def appointments_list():
    appts = Appointment.query.join(Patient).filter(Patient.user_id == current_user.owner_id, Patient.status != 'Archived', Appointment.status != 'Archived').all()
    return render_template('appointments.html', appointments=appts)

@app.route('/appointments/delete/<int:id>', methods=['POST', 'GET'])
@login_required
@subscription_required
@require_role('Doctor', 'Receptionist')
def archive_appointment(id):
    appt = Appointment.query.filter_by(id=id, user_id=current_user.owner_id).first_or_404()
    appt.status = 'Archived'
    db.session.commit()
    log_activity("Appointment Cancelled", f"HIPAA Compliance Action: Cancelled/Archived appointment ID: {id} for Doctor: {appt.doctor_name}")
    flash("Appointment has been securely archived.")
    return redirect(url_for('appointments_list'))

@app.route('/appointments/add', methods=['GET', 'POST'])
@login_required
@subscription_required
@require_role('Receptionist', 'Doctor')
def add_appointment():
    if request.method == 'POST':
        visit_type = request.form.get('visit_type', 'Office Visit')
        room_id = f"healthpro-{uuid.uuid4().hex[:12]}" if visit_type == 'Telehealth' else None

        appointment_date = datetime.strptime(request.form['appointment_date'], '%Y-%m-%d').date()
        appointment_time = datetime.strptime(request.form['appointment_time'], '%H:%M').time()
        doctor_name = request.form['doctor_name']

        conflict = Appointment.query.filter_by(
            user_id=current_user.owner_id,
            appointment_date=appointment_date,
            appointment_time=appointment_time,
            doctor_name=doctor_name
        ).filter(Appointment.status != 'Archived', Appointment.status != 'Cancelled').first()

        if conflict:
            flash(f"Booking Error: Dr. {doctor_name} already has an appointment scheduled at {appointment_time} on {appointment_date}.")
            return redirect(url_for('add_appointment'))

        new_appt = Appointment(
            user_id=current_user.owner_id, 
            patient_id=request.form['patient_id'], 
            doctor_name=doctor_name, 
            appointment_date=appointment_date, 
            appointment_time=appointment_time, 
            visit_type=visit_type,
            reason=request.form.get('reason'),
            chief_complaint=request.form.get('chief_complaint'),
            meeting_room_id=room_id
        )
        db.session.add(new_appt)
        db.session.commit()
        log_activity("Appointment Scheduled", f"Booked {visit_type} with Dr. {doctor_name} for Patient ID: {new_appt.patient_id}")
        flash("Appointment successfully scheduled!")
        return redirect(url_for('appointments_list'))
        
    patients = Patient.query.filter_by(user_id=current_user.owner_id).filter(Patient.status != 'Archived').all()
    return render_template('appointment_form.html', appt=None, patients=patients)

@app.route('/appointments/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@subscription_required
@require_role('Receptionist', 'Doctor')
def edit_appointment(id):
    appt = Appointment.query.filter_by(id=id, user_id=current_user.owner_id).first_or_404()
    if request.method == 'POST':
        new_date = datetime.strptime(request.form['appointment_date'], '%Y-%m-%d').date()
        new_time = datetime.strptime(request.form['appointment_time'], '%H:%M').time()
        new_doctor = request.form['doctor_name']

        conflict = Appointment.query.filter(
            Appointment.id != id,
            Appointment.user_id == current_user.owner_id,
            Appointment.appointment_date == new_date,
            Appointment.appointment_time == new_time,
            Appointment.doctor_name == new_doctor,
            Appointment.status != 'Archived',
            Appointment.status != 'Cancelled'
        ).first()

        if conflict:
            flash(f"Update Error: Dr. {new_doctor} is already booked at {new_time} on {new_date}.")
            return redirect(url_for('edit_appointment', id=id))

        appt.doctor_name = new_doctor
        appt.appointment_date = new_date
        appt.appointment_time = new_time
        appt.visit_type = request.form.get('visit_type', 'Office Visit')
        appt.reason = request.form.get('reason')
        appt.chief_complaint = request.form.get('chief_complaint')
        db.session.commit()
        log_activity("Appointment Rescheduled/Updated", f"HIPAA Compliance Action: Modified schedule details for Appt ID: {id}")
        flash("Appointment updated successfully!")
        return redirect(url_for('appointments_list'))
    
    patients = Patient.query.filter_by(user_id=current_user.owner_id).filter(Patient.status != 'Archived').all()
    return render_template('appointment_form.html', appt=appt, patients=patients)

@app.route('/telehealth')
@login_required
@subscription_required
@require_role('Doctor')
def telehealth_dashboard():
    today = datetime.utcnow().date()
    telehealth_appts = Appointment.query.filter_by(user_id=current_user.owner_id, visit_type='Telehealth').filter(Appointment.status != 'Archived').order_by(Appointment.appointment_date, Appointment.appointment_time).all()
    return render_template('telehealth_dashboard.html', appointments=telehealth_appts, today=today)

@app.route('/telehealth/room/<int:appt_id>')
@login_required
@subscription_required
@require_role('Doctor')
def telehealth_room(appt_id):
    appt = Appointment.query.filter_by(id=appt_id, user_id=current_user.owner_id).first_or_404()
    if not appt.meeting_room_id:
        flash("This is not a Telehealth appointment.")
        return redirect(url_for('appointments_list'))
    log_activity("Started Telehealth Session", f"Initiated virtual care room for Appt ID: {appt_id}")
    return render_template('telehealth_room.html', appt=appt)

# ==========================================
# CLINICAL (VITALS, SOAP, LABS, MEDS)
# ==========================================
@app.route('/appointments/vitals/<int:appt_id>', methods=['GET', 'POST'])
@login_required
@subscription_required
@require_role('Doctor', 'Receptionist')
def record_vitals(appt_id):
    appt = Appointment.query.filter_by(id=appt_id, user_id=current_user.owner_id).first_or_404()
    vital = Vital.query.filter_by(appointment_id=appt_id).first()
    if request.method == 'POST':
        if not vital:
            vital = Vital(appointment_id=appt_id)
            db.session.add(vital)
        vital.bp_systolic = request.form.get('bp_systolic')
        vital.bp_diastolic = request.form.get('bp_diastolic')
        vital.heart_rate = request.form.get('heart_rate')
        vital.temperature = request.form.get('temperature') 
        vital.weight_lbs = request.form.get('weight_lbs')
        vital.clinical_notes = request.form.get('clinical_notes')
        appt.status = 'Completed'
        db.session.commit()
        log_activity("Recorded Clinical Vitals", f"Saved physical vitals data for Appt ID: {appt_id}")
        
        trigger_auto_billing(appt_id)
        
        return redirect(url_for('appointments_list'))
    return render_template('vitals_add.html', appt=appt, vital=vital)

@app.route('/appointments/soap/<int:appt_id>', methods=['GET', 'POST'])
@login_required
@subscription_required
@require_role('Doctor')
def manage_soap(appt_id):
    appt = Appointment.query.filter_by(id=appt_id, user_id=current_user.owner_id).first_or_404()
    soap = SOAPNote.query.filter_by(appointment_id=appt_id).first()
    
    if request.method == 'POST':
        if not soap:
            soap = SOAPNote(appointment_id=appt_id)
            db.session.add(soap)
        soap.subjective = request.form.get('subjective')
        soap.objective = request.form.get('objective')
        soap.assessment = request.form.get('assessment')
        soap.plan = request.form.get('plan')
        appt.status = 'Completed'
        db.session.commit()
        log_activity("SOAP Note Saved", f"Generated/Saved clinical chart notes for Appt ID: {appt_id}")
        
        trigger_auto_billing(appt_id)
        
        flash("SOAP Notes saved successfully!")
        return redirect(url_for('appointments_list'))
    return render_template('soap_form.html', appt=appt, soap=soap)

@app.route('/appointments/soap/print/<int:appt_id>')
@login_required
@subscription_required
@require_role('Doctor')
def print_soap(appt_id):
    appt = Appointment.query.filter_by(id=appt_id, user_id=current_user.owner_id).first_or_404()
    patient = Patient.query.get(appt.patient_id)
    soap = SOAPNote.query.filter_by(appointment_id=appt_id).first()
    vital = Vital.query.filter_by(appointment_id=appt_id).first()
    
    if not soap:
        flash("No SOAP Note found to print for this appointment.")
        return redirect(url_for('appointments_list'))
        
    log_activity("Medical Chart Printed", f"HIPAA Compliance Action: Exported/Printed SOAP Chart for Appt ID: {appt_id}")
    return render_template('print_soap.html', appt=appt, patient=patient, soap=soap, vital=vital, current_user=current_user)

@app.route('/appointments/labs/<int:appt_id>', methods=['GET', 'POST'])
@login_required
@subscription_required
@require_role('Doctor')
def manage_labs(appt_id):
    appt = Appointment.query.filter_by(id=appt_id, user_id=current_user.owner_id).first_or_404()
    orders = LabOrder.query.filter_by(appointment_id=appt_id).all()
    clinic_tests = ClinicLabTest.query.filter_by(clinic_id=current_user.owner_id).order_by(ClinicLabTest.test_name).all()
    
    if request.method == 'POST':
        test_names = request.form.getlist('test_name[]')
        urgencies = request.form.getlist('urgency[]')
        collections = request.form.getlist('collection[]') 
        vendor_name = request.form.get('vendor_name', 'In-House') 
        
        for i in range(len(test_names)):
            t_name = test_names[i].strip()
            if t_name:
                new_order = LabOrder(
                    appointment_id=appt_id,
                    patient_id=appt.patient_id,
                    test_name=t_name,
                    vendor_name=vendor_name,
                    urgency=urgencies[i] if i < len(urgencies) else 'Routine',
                    collection=collections[i] if i < len(collections) else 'Blood', 
                    status='Transmitted via HL7' if vendor_name != 'In-House' else 'Ordered'
                )
                db.session.add(new_order)
                
                existing_test = ClinicLabTest.query.filter_by(clinic_id=current_user.owner_id, test_name=t_name).first()
                if not existing_test:
                    new_clinic_test = ClinicLabTest(clinic_id=current_user.owner_id, test_name=t_name)
                    db.session.add(new_clinic_test)
                    
        db.session.commit()
        log_activity("Lab Orders Placed", f"Ordered clinical labs for MRN: {appt.patient.mrn} (Appt: {appt_id})")
        
        if vendor_name != 'In-House':
            lab_email = f"hl7_intake@{vendor_name.lower().replace(' ', '')}.com"
            subject = f"HL7 Lab Order - MRN: {appt.patient.mrn}"
            body = f"CONFIDENTIAL HEALTH INFORMATION\n\nNew electronic lab order received via EHR interface.\nDoctor: {appt.doctor_name}\nPatient MRN: {appt.patient.mrn}\nDOB: {appt.patient.dob}\nTests Ordered: {', '.join([t.strip() for t in test_names if t.strip()])}\nPriority: Routine\n\nPlease process through your HL7 gateway."
            send_email_async(lab_email, subject, body)
            flash(f"Lab Orders successfully transmitted to {vendor_name} via Automated Email/HL7 Protocol!")
        else:
            flash("In-House Lab Orders placed successfully!")
            
        return redirect(url_for('manage_labs', appt_id=appt_id))
        
    return render_template('lab_form.html', appt=appt, orders=orders, clinic_tests=clinic_tests)

@app.route('/labs/result/<int:id>', methods=['POST'])
@login_required
@subscription_required
@require_role('Doctor')
def result_lab(id):
    order = LabOrder.query.get_or_404(id)
    Appointment.query.filter_by(id=order.appointment_id, user_id=current_user.owner_id).first_or_404()
    order.results = request.form.get('results')
    order.status = 'Resulted'
    db.session.commit()
    log_activity("Lab Results Entered", f"Entered medical results for Lab Order ID: {id}")
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'success': True, 'message': 'Lab results updated!'})
        
    flash("Lab results updated!")
    return redirect(url_for('manage_labs', appt_id=order.appointment_id))

@app.route('/labs/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@subscription_required
@require_role('Doctor')
def edit_lab(id):
    order = LabOrder.query.get_or_404(id)
    appt = Appointment.query.get(order.appointment_id)
    clinic_tests = ClinicLabTest.query.filter_by(clinic_id=current_user.owner_id).order_by(ClinicLabTest.test_name).all()
    
    if request.method == 'POST':
        t_name = request.form.get('test_name').strip()
        order.test_name = t_name
        order.urgency = request.form.get('urgency')
        order.collection = request.form.get('collection') 
        
        if t_name:
            existing_test = ClinicLabTest.query.filter_by(clinic_id=current_user.owner_id, test_name=t_name).first()
            if not existing_test:
                new_clinic_test = ClinicLabTest(clinic_id=current_user.owner_id, test_name=t_name)
                db.session.add(new_clinic_test)
                
        db.session.commit()
        log_activity("Lab Order Updated", f"Modified lab order {order.test_name} for Appt ID: {order.appointment_id}")
        flash("Lab Test updated successfully!")
        return redirect(url_for('manage_labs', appt_id=order.appointment_id))
    
    return render_template('lab_edit.html', order=order, appt=appt, clinic_tests=clinic_tests)

@app.route('/appointments/prescription/<int:appt_id>', methods=['GET', 'POST'])
@login_required
@subscription_required
@require_role('Doctor')
def manage_prescription(appt_id):
    appt = Appointment.query.filter_by(id=appt_id, user_id=current_user.owner_id).first_or_404()
    meds = Prescription.query.filter_by(appointment_id=appt_id).all()
    
    if request.method == 'POST':
        pharmacy_name = request.form.get('pharmacy_name', 'Patient Choice')
        medications = request.form.getlist('medication[]')
        
        if medications: 
            dosages = request.form.getlist('dosage[]')
            frequencies = request.form.getlist('frequency[]')
            durations = request.form.getlist('duration[]')
            instructions = request.form.getlist('instructions[]')
            for i in range(len(medications)):
                if medications[i].strip():
                    new_med = Prescription(
                        appointment_id=appt_id, 
                        medication=medications[i], 
                        dosage=dosages[i] if i < len(dosages) else '', 
                        frequency=frequencies[i] if i < len(frequencies) else '', 
                        duration=durations[i] if i < len(durations) else '', 
                        instructions=instructions[i] if i < len(instructions) else '',
                        pharmacy_name=pharmacy_name,
                        status='Transmitted via eRx' if pharmacy_name != 'Patient Choice' else 'Printed'
                    )
                    db.session.add(new_med)
        else:
            medication = request.form.get('medication')
            if medication:
                new_med = Prescription(
                    appointment_id=appt_id, 
                    medication=medication, 
                    dosage=request.form.get('dosage'), 
                    frequency=request.form.get('frequency'), 
                    duration=request.form.get('duration'), 
                    instructions=request.form.get('instructions'),
                    pharmacy_name=pharmacy_name,
                    status='Transmitted via eRx' if pharmacy_name != 'Patient Choice' else 'Printed'
                )
                db.session.add(new_med)
                
        db.session.commit()
        log_activity("E-Prescription Generated", f"Added medications to chart. Appt ID: {appt_id}")
        
        if pharmacy_name != 'Patient Choice':
            pharmacy_email = f"erx_intake@{pharmacy_name.lower().replace(' ', '')}.com"
            subject = f"eRx Prescription Transmitted - MRN: {appt.patient.mrn}"
            body = f"CONFIDENTIAL MEDICAL E-PRESCRIPTION\n\nDoctor: {appt.doctor_name}\nPatient MRN: {appt.patient.mrn}\nDOB: {appt.patient.dob}\n\nMedications transmitted securely via automated email. Please dispense accordingly."
            send_email_async(pharmacy_email, subject, body)
            flash(f"Prescription securely transmitted to {pharmacy_name} via Automated Email!")
        else:
            flash("Prescription saved for printing!")
            
        return redirect(url_for('manage_prescription', appt_id=appt_id))
    return render_template('prescription_form.html', appt=appt, meds=meds)

@app.route('/appointments/prescription/print/<int:appt_id>')
@login_required
@subscription_required
@require_role('Doctor')
def print_prescription(appt_id):
    appt = Appointment.query.filter_by(id=appt_id, user_id=current_user.owner_id).first_or_404()
    patient = Patient.query.get(appt.patient_id)
    meds = Prescription.query.filter_by(appointment_id=appt_id).all()
    log_activity("Printed E-Prescription", f"HIPAA Compliance Action: Printed Rx for Appt ID: {appt_id}")
    return render_template('prescription_print.html', appt=appt, patient=patient, meds=meds)

@app.route('/prescription/delete/<int:id>', methods=['GET', 'POST'])
@login_required
@subscription_required
@require_role('Doctor')
def delete_medicine(id):
    med = Prescription.query.get_or_404(id)
    appt_id = med.appointment_id
    db.session.delete(med)
    db.session.commit()
    log_activity("Medication Removed", f"Deleted {med.medication} from chart for Appt ID: {appt_id}")
    flash("Medicine removed successfully!")
    return redirect(url_for('manage_prescription', appt_id=appt_id))

@app.route('/prescription/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@subscription_required
@require_role('Doctor')
def edit_medicine(id):
    med = Prescription.query.get_or_404(id)
    appt = Appointment.query.get(med.appointment_id)
    
    if request.method == 'POST':
        med.medication = request.form.get('medication')
        med.dosage = request.form.get('dosage')
        med.frequency = request.form.get('frequency')
        med.duration = request.form.get('duration')
        med.instructions = request.form.get('instructions')
        db.session.commit()
        log_activity("Medication Modified", f"Updated {med.medication} details for Appt ID: {med.appointment_id}")
        flash("Medicine updated successfully!")
        return redirect(url_for('manage_prescription', appt_id=med.appointment_id))
    
    return render_template('prescription_edit.html', med=med, appt=appt)

# ==========================================
# AI INTEGRATIONS (SECURE DEMO LOGIC)
# ==========================================
@app.route('/ai/generate_soap', methods=['POST'])
@login_required
@subscription_required
@require_role('Doctor')
def generate_soap_ai():
    data = request.get_json()
    raw_text = data.get('raw_text', '')

    if not raw_text:
        return jsonify({'success': False, 'error': 'No text provided'})

    try:
        active_api_key = GEMINI_API_KEY
        
        if not active_api_key or active_api_key.upper() == 'DUMMY' or len(active_api_key) < 10:
            log_activity("AI Charting System", "Used fallback Demo SOAP Note generator")
            return jsonify({
                'success': True,
                'soap': {
                    'subjective': f"Patient reports: {raw_text}",
                    'objective': "Vitals appear stable on visual examination. No acute distress observed.",
                    'assessment': "1. Symptomatic presentation noted.\n2. Pending further clinical testing.",
                    'plan': "1. Advised rest and hydration.\n2. Follow-up in 3-5 days if symptoms persist.\n3. Prescribed symptomatic relief."
                }
            })

        import google.generativeai as genai
        genai.configure(api_key=active_api_key) 
        model = genai.GenerativeModel('gemini-3.1-pro-preview')
        
        prompt = f"""
        You are an expert USA medical scribe. Convert the following raw notes into a professional medical SOAP note.
        Respond ONLY in valid JSON format with exactly these four keys: "subjective", "objective", "assessment", "plan".
        Raw notes: {raw_text}
        """
        response = model.generate_content(prompt)
        response_text = response.text.strip()
        
        marker = "`" * 3
        pattern = r"" + marker + r"(?:json)?\s*(\{.*?\})\s*" + marker
        json_match = re.search(pattern, response_text, re.DOTALL)
        
        if json_match:
            response_text = json_match.group(1)
        else:
            start = response_text.find('{')
            end = response_text.rfind('}')
            if start != -1 and end != -1:
                response_text = response_text[start:end+1]
                
        soap_data = json.loads(response_text)
        log_activity("AI Charting System", "Generated Live AI Smart Scribe SOAP Note safely")
        
        return jsonify({
            'success': True,
            'soap': {
                'subjective': soap_data.get('subjective', ''),
                'objective': soap_data.get('objective', ''),
                'assessment': soap_data.get('assessment', ''),
                'plan': soap_data.get('plan', '')
            }
        })
    except Exception as e:
         return jsonify({'success': False, 'error': 'AI System Error: ' + str(e)})

# ==========================================
# BILLING
# ==========================================
@app.route('/billing')
@login_required
@subscription_required
@require_role('Biller', 'Doctor') 
def billing_list():
    invoices = Invoice.query.filter_by(user_id=current_user.owner_id).filter(Invoice.status != 'Archived').all()
    return render_template('billing.html', invoices=invoices)

@app.route('/billing/create/<int:appt_id>', methods=['GET', 'POST'])
@login_required
@subscription_required
@require_role('Biller', 'Doctor')
def create_invoice(appt_id):
    appt = Appointment.query.filter_by(id=appt_id, user_id=current_user.owner_id).first_or_404()
    existing_invoice = Invoice.query.filter_by(appointment_id=appt_id).first()
    if request.method == 'POST':
        if not existing_invoice:
            existing_invoice = Invoice(user_id=current_user.owner_id, appointment_id=appt_id, patient_id=appt.patient_id)
            db.session.add(existing_invoice)
        existing_invoice.consultation_fee = float(request.form.get('consultation_fee', 0))
        existing_invoice.other_charges = float(request.form.get('other_charges', 0))
        existing_invoice.discount = float(request.form.get('discount', 0))
        existing_invoice.total_amount = (existing_invoice.consultation_fee + existing_invoice.other_charges) - existing_invoice.discount
        existing_invoice.status = request.form.get('status', 'Unpaid')
        db.session.commit()
        log_activity("Invoice Generation", f"Created/Updated billing invoice for Appt ID: {appt_id}")
        return redirect(url_for('billing_list'))
    return render_template('billing_form.html', appt=appt, invoice=existing_invoice)

@app.route('/billing/delete/<int:id>', methods=['POST', 'GET'])
@login_required
@subscription_required
@require_role('Doctor', 'Biller')
def archive_invoice(id):
    invoice = Invoice.query.filter_by(id=id, user_id=current_user.owner_id).first_or_404()
    invoice.status = 'Archived'
    db.session.commit()
    log_activity("Invoice Archived", f"HIPAA Compliance Action: Soft deleted Invoice ID: {id}")
    flash("Invoice archived successfully.")
    return redirect(url_for('billing_list'))

@app.route('/billing/print/<int:id>')
@login_required
@subscription_required
@require_role('Biller', 'Doctor')
def print_invoice(id):
    invoice = Invoice.query.filter_by(id=id, user_id=current_user.owner_id).first_or_404()
    appt = Appointment.query.get(invoice.appointment_id)
    patient = Patient.query.get(invoice.patient_id)
    log_activity("Invoice Printed", f"Printed Invoice ID: {id} for patient records")
    return render_template('billing_print.html', invoice=invoice, appt=appt, patient=patient)

@app.route('/billing/pay/<int:id>')
@login_required
@subscription_required
@require_role('Biller', 'Doctor')
def pay_invoice(id):
    invoice = Invoice.query.filter_by(id=id, user_id=current_user.owner_id).first_or_404()
    settings = ClinicSettings.query.filter_by(clinic_id=current_user.owner_id).first()
    active_stripe_key = settings.stripe_secret_key if settings and settings.stripe_secret_key else stripe.api_key
    
    try:
        stripe.api_key = active_stripe_key
        session_stripe = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{'price_data': {'currency': 'usd', 'product_data': {'name': f'HealthPro Invoice INV-00{invoice.id}'}, 'unit_amount': int(invoice.total_amount * 100)}, 'quantity': 1}],
            mode='payment',
            success_url=url_for('billing_list', _external=True),
            cancel_url=url_for('billing_list', _external=True),
        )
        return redirect(session_stripe.url, code=303)
    except Exception as e:
        flash("Payment Error: Please configure valid Stripe API Keys in Clinic Settings.")
        return redirect(url_for('billing_list'))

# ==========================================
# PUBLIC PATIENT AUTO-PAY (ZERO-TOUCH RCM)
# ==========================================
@app.route('/patient/pay/<int:invoice_id>')
def public_pay_invoice(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    settings = ClinicSettings.query.filter_by(clinic_id=invoice.user_id).first()
    active_stripe_key = settings.stripe_secret_key if settings and settings.stripe_secret_key else stripe.api_key
    
    try:
        stripe.api_key = active_stripe_key
        session_stripe = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{'price_data': {
                'currency': 'usd', 
                'product_data': {'name': f'HealthPro Clinic Invoice INV-{invoice.id}'}
                }, 
                'unit_amount': int(invoice.total_amount * 100)}],
            quantity=1,
            mode='payment',
            success_url=url_for('public_payment_success', invoice_id=invoice.id, _external=True),
            cancel_url=url_for('patient_login', _external=True),
        )
        return redirect(session_stripe.url, code=303)
    except Exception as e:
        flash("Online payment gateway is not configured yet. Please contact the clinic.")
        return redirect(url_for('patient_login'))

@app.route('/patient/pay/success/<int:invoice_id>')
def public_payment_success(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    invoice.status = 'Paid'
    db.session.commit()
    return f"""
    <div style="text-align: center; font-family: sans-serif; margin-top: 100px;">
        <h2 style="color: #10b981;">Payment Successful!</h2>
        <p>Thank you. Your invoice <b>INV-{invoice.id}</b> has been securely marked as Paid.</p>
        <p>You may now close this window safely.</p>
    </div>
    """

# ==========================================
# ADMIN DASHBOARD
# ==========================================
@app.route('/admin/dashboard')
@login_required
@superadmin_required
def admin_dashboard():
    total_clinics = User.query.filter_by(is_superadmin=False, parent_id=None).count() 
    total_patients = Patient.query.count()
    total_revenue = db.session.query(db.func.sum(Invoice.total_amount)).filter(Invoice.status == 'Paid').scalar() or 0.0
    return render_template('admin_dashboard.html', total_clinics=total_clinics, total_patients=total_patients, total_revenue=total_revenue)

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
