import os
import random
import smtplib
import logging
import uuid
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, send_file
)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'default-secret-key')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max upload

# ==================== LOGGING SETUP ====================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== SUPABASE SETUP ====================
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in .env file")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==================== ALLOWED FILE EXTENSIONS ====================
ALLOWED_EXTENSIONS = {
    'png', 'jpg', 'jpeg', 'gif', 'webp',  # Images
    'pdf', 'doc', 'docx', 'xls', 'xlsx',  # Documents
    'ppt', 'pptx', 'txt', 'zip'            # Other files
}

def allowed_file(filename):
    """Check if file extension is allowed"""
    if not filename or '.' not in filename:
        return False
    return filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ==================== SUPABASE STORAGE HELPERS ====================
def upload_file(bucket_name, file_data, original_filename):
    """
    Upload file to Supabase Storage
    
    Args:
        bucket_name: Name of the storage bucket (avatars, notes, exercises, etc.)
        file_data: The file object from request.files
        original_filename: Original filename from the uploaded file
    
    Returns:
        Public URL of the uploaded file, or None if upload fails
    """
    try:
        # Validation
        if not file_data or not file_data.filename:
            logger.warning(f"No file provided for upload to bucket '{bucket_name}'")
            return None
        
        if not allowed_file(file_data.filename):
            logger.warning(f"File type not allowed: {file_data.filename} for bucket '{bucket_name}'")
            return None
        
        # Read file content
        file_content = file_data.read()
        if not file_content:
            logger.warning(f"Empty file: {file_data.filename}")
            return None
        
        # Generate unique filename
        file_ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
        unique_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}.{file_ext}"
        
        # Upload to Supabase Storage
        logger.info(f"Uploading file '{original_filename}' to bucket '{bucket_name}' as '{unique_filename}'")
        supabase.storage.from_(bucket_name).upload(
            unique_filename,
            file_content,
            {'content-type': file_data.content_type or 'application/octet-stream'}
        )
        
        # Get public URL
        public_url = supabase.storage.from_(bucket_name).get_public_url(unique_filename)
        
        logger.info(f"File uploaded successfully: {public_url}")
        return public_url
        
    except Exception as e:
        logger.error(f"Upload Error to bucket '{bucket_name}': {str(e)}")
        return None

def delete_storage_file(file_url):
    """
    Delete file from Supabase Storage using its public URL
    
    Args:
        file_url: Full public URL of the file
    
    Returns:
        True if deletion successful, False otherwise
    """
    try:
        if not file_url:
            logger.warning("No file URL provided for deletion")
            return False
        
        # Check if it's a Supabase Storage URL
        if "storage/v1/object/public/" not in file_url:
            logger.warning(f"URL is not a Supabase Storage public URL: {file_url}")
            return False
        
        # Extract bucket and filename from URL
        # URL format: https://PROJECT_ID.supabase.co/storage/v1/object/public/bucket_name/filename
        try:
            # Split by '/public/'
            url_parts = file_url.split('/public/')
            if len(url_parts) != 2:
                logger.warning(f"Invalid storage URL format: {file_url}")
                return False
            
            bucket_and_file = url_parts[1]
            bucket_name = bucket_and_file.split('/')[0]
            filename = '/'.join(bucket_and_file.split('/')[1:])
            
            if not bucket_name or not filename:
                logger.warning(f"Could not extract bucket or filename from URL: {file_url}")
                return False
            
            # Delete from Supabase Storage
            logger.info(f"Deleting file '{filename}' from bucket '{bucket_name}'")
            supabase.storage.from_(bucket_name).remove([filename])
            
            logger.info(f"File deleted successfully from bucket '{bucket_name}': {filename}")
            return True
            
        except Exception as e:
            logger.error(f"Error parsing storage URL {file_url}: {str(e)}")
            return False
        
    except Exception as e:
        logger.error(f"Delete Error for {file_url}: {str(e)}")
        return False

def get_public_url(bucket_name, filename):
    """Get public URL for a file in Supabase Storage"""
    try:
        return supabase.storage.from_(bucket_name).get_public_url(filename)
    except Exception as e:
        logger.error(f"Error getting public URL: {e}")
        return None

# ==================== SMTP CONFIGURATION ====================
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
SMTP_EMAIL = os.getenv('SMTP_EMAIL')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD')

if not SMTP_EMAIL or not SMTP_PASSWORD:
    logger.warning("SMTP credentials not configured. Email sending will fail.")

# ==================== SUPABASE DATABASE HELPERS ====================
def get_db():
    """Return supabase client for compatibility"""
    return supabase

def db_get(table, filters=None):
    """Get records from Supabase table"""
    try:
        query = supabase.table(table).select('*')
        if filters:
            for key, value in filters.items():
                query = query.eq(key, value)
        response = query.execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"GET Error on {table}: {e}")
        return []

def db_get_by_id(table, record_id):
    """Get single record by ID"""
    try:
        response = supabase.table(table).select('*').eq('id', record_id).execute()
        if response.data and len(response.data) > 0:
            return response.data[0]
        return None
    except Exception as e:
        logger.error(f"GET by ID Error on {table}: {e}")
        return None

def db_insert(table, data):
    """Insert record into Supabase table"""
    try:
        if 'id' not in data:
            data['id'] = str(uuid.uuid4())
        
        # Convert datetime objects to strings
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        
        response = supabase.table(table).insert(data).execute()
        if response.data and len(response.data) > 0:
            return response.data[0]
        return None
    except Exception as e:
        logger.error(f"INSERT Error on {table}: {e}")
        return None

def db_update(table, record_id, data):
    """Update record in Supabase table"""
    try:
        # Convert datetime objects to strings
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        
        response = supabase.table(table).update(data).eq('id', record_id).execute()
        if response.data and len(response.data) > 0:
            return response.data[0]
        return None
    except Exception as e:
        logger.error(f"UPDATE Error on {table}: {e}")
        return None

def db_delete(table, record_id):
    """Delete record from Supabase table"""
    try:
        response = supabase.table(table).delete().eq('id', record_id).execute()
        return len(response.data) > 0 if response.data else False
    except Exception as e:
        logger.error(f"DELETE Error on {table}: {e}")
        return False

# ==================== UTILITY FUNCTIONS ====================
def safe_int(value, default=0):
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default

def safe_str(value, default=''):
    if value is None:
        return default
    return str(value)

def format_date(value):
    if not value:
        return '-'
    try:
        if isinstance(value, str):
            if 'T' in value:
                dt = datetime.fromisoformat(value.replace('Z', '+00:00').split('+')[0].split('.')[0])
                return dt.strftime('%d %b %Y, %I:%M %p')
            elif len(value) >= 10:
                return value[:10]
        elif isinstance(value, datetime):
            return value.strftime('%d %b %Y, %I:%M %p')
        return str(value)[:16] if value else '-'
    except:
        return str(value)[:16] if value else '-'

def format_time_ago(value):
    if not value:
        return '-'
    try:
        if isinstance(value, str):
            if 'T' in value:
                dt = datetime.fromisoformat(value.replace('Z', '+00:00').split('+')[0].split('.')[0])
            else:
                return value
        elif isinstance(value, datetime):
            dt = value
        else:
            return str(value)
        now = datetime.now()
        diff = now - dt
        if diff.days > 365:
            return f"{diff.days // 365}y ago"
        elif diff.days > 30:
            return f"{diff.days // 30}mo ago"
        elif diff.days > 0:
            return f"{diff.days}d ago"
        elif diff.seconds > 3600:
            return f"{diff.seconds // 3600}h ago"
        elif diff.seconds > 60:
            return f"{diff.seconds // 60}m ago"
        else:
            return "Just now"
    except:
        return str(value)[:16] if value else '-'

def get_batch_from_joined_month(joined_month):
    if not joined_month:
        return f"{datetime.now().strftime('%B %Y')} Batch"
    return f"{joined_month} Batch"

def is_demo_true(value):
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).lower() in ['true', '1', 'on', 'yes']

def is_url(value):
    if not value:
        return False
    return str(value).startswith('http://') or str(value).startswith('https://')

def generate_otp():
    return str(random.randint(100000, 999999))

def send_otp_email(email, otp, purpose="verification"):
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        logger.warning(f"SMTP not configured. OTP for {email}: {otp}")
        return True
    
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_EMAIL
        msg['To'] = email
        msg['Subject'] = f"Slokam Technology - {purpose.title()} OTP"
        body = f"""
        <h2>Slokam Technology</h2>
        <h3>{purpose.title()} OTP</h3>
        <p>Your OTP for {purpose} is:</p>
        <h1>{otp}</h1>
        <p>This OTP is valid for 5 minutes.</p>
        <p>If you didn't request this, please ignore.</p>
        """
        msg.attach(MIMEText(body, 'html'))
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        logger.info(f"OTP email sent to {email}")
        return True
    except Exception as e:
        logger.error(f"Email error: {e}")
        logger.info(f"⚠ OTP for {email}: {otp} (use this for testing)")
        return True

app.jinja_env.globals['format_date'] = format_date
app.jinja_env.globals['format_time_ago'] = format_time_ago
app.jinja_env.globals['is_demo_true'] = is_demo_true
app.jinja_env.globals['is_url'] = is_url

# ==================== BUSINESS LOGIC HELPERS ====================
def generate_student_id():
    students = db_get('students')
    count = len(students) + 1 if students else 1
    return f"STU2026{count:04d}"

def get_joined_month():
    return datetime.now().strftime("%B %Y")

def update_login_streak(student):
    student_id = student['id']
    now = datetime.now().strftime('%Y-%m-%d')
    last_login = student.get('last_login')
    streak = safe_int(student.get('login_streak'), 0)
    if last_login and str(last_login) not in ['None', '', 'null']:
        try:
            last_str = str(last_login)[:10]
            last_date = datetime.strptime(last_str, '%Y-%m-%d').date()
            today = datetime.now().date()
            diff = (today - last_date).days
            if diff == 0:
                pass
            elif diff == 1:
                streak += 1
            else:
                streak = 1
        except:
            streak = 1
    else:
        streak = 1
    log_student_activity(student_id, 'login', f'Login streak: {streak}')
    db_update('students', student_id, {'login_streak': streak, 'last_login': now})
    return streak

def log_student_activity(student_object_id, action_type, description):
    try:
        db_insert('student_activities', {
            'student_object_id': student_object_id,
            'action_type': action_type,
            'description': description,
            'created_at': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Log activity error: {e}")

def get_admin_details(admin_email):
    admins = db_get('admin_details', {'email': admin_email})
    return admins[0] if admins else None

def create_or_update_admin_details(admin_email, admin_name=None, admin_avatar=None):
    existing_admin = get_admin_details(admin_email)
    if existing_admin:
        update_data = {'updated_at': datetime.now().isoformat()}
        if admin_name is not None:
            update_data['name'] = admin_name
        if admin_avatar is not None:
            update_data['avatar'] = admin_avatar
        if update_data:
            return db_update('admin_details', existing_admin['id'], update_data)
    else:
        admin_data = {
            'email': admin_email,
            'name': admin_name if admin_name else 'Administrator',
            'avatar': admin_avatar if admin_avatar else '',
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }
        return db_insert('admin_details', admin_data)
    return None

# ==================== SESSION DECORATORS ====================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login first.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'admin_id' not in session:
            flash('Admin login required.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

# ==================== PUBLIC ROUTES ====================
@app.route('/')
def index():
    return render_template('base.html')

# ==================== REGISTRATION FLOW ====================
@app.route('/register', methods=['POST'])
def register():
    fullname = request.form.get('fullname', '').strip()
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '').strip()
    confirm_password = request.form.get('confirm_password', '').strip()
    passed_out_year = request.form.get('passed_out_year', '').strip()
    joined_month_input = request.form.get('joined_month', '').strip()
    college_name = request.form.get('college_name', '').strip()
    mobile_number = request.form.get('mobile_number', '').strip()
    parent_mobile = request.form.get('parent_mobile', '').strip()
    qualification = request.form.get('qualification', '').strip()

    if not all([fullname, email, password, confirm_password]):
        flash('Full name, email, and password are required.', 'error')
        return redirect(url_for('index'))
    if password != confirm_password:
        flash('Passwords do not match.', 'error')
        return redirect(url_for('index'))
    if len(password) < 6:
        flash('Password must be at least 6 characters.', 'error')
        return redirect(url_for('index'))

    existing = db_get('students', {'email': email})
    if existing and len(existing) > 0:
        flash('Email already registered.', 'error')
        return redirect(url_for('index'))

    otp = generate_otp()
    send_otp_email(email, otp, "email verification")
    joined_month = joined_month_input if joined_month_input else get_joined_month()

    session['reg_data'] = {
        'fullname': fullname,
        'email': email,
        'password': generate_password_hash(password),
        'passed_out_year': passed_out_year,
        'joined_month': joined_month,
        'college_name': college_name,
        'mobile_number': mobile_number,
        'parent_mobile': parent_mobile,
        'qualification': qualification
    }
    session['reg_otp'] = otp
    session['reg_otp_time'] = datetime.now().isoformat()
    flash('OTP sent to your email. Please verify to complete registration.', 'info')
    return redirect(url_for('index'))

@app.route('/verify-register-otp', methods=['POST'])
def verify_register_otp():
    user_otp = request.form.get('otp', '').strip()
    stored_otp = session.get('reg_otp', '')
    otp_time = session.get('reg_otp_time', '')
    reg_data = session.get('reg_data', {})

    if not user_otp:
        flash('Please enter OTP.', 'error')
        return redirect(url_for('index'))
    if not stored_otp or not reg_data:
        flash('Session expired. Please register again.', 'error')
        return redirect(url_for('index'))
    if otp_time:
        try:
            otp_dt = datetime.fromisoformat(otp_time)
            if (datetime.now() - otp_dt).total_seconds() > 300:
                session.pop('reg_otp', None)
                session.pop('reg_otp_time', None)
                session.pop('reg_data', None)
                flash('OTP expired. Please register again.', 'error')
                return redirect(url_for('index'))
        except:
            pass
    if user_otp != stored_otp:
        flash('Invalid OTP. Please try again.', 'error')
        return redirect(url_for('index'))

    batch = get_batch_from_joined_month(reg_data.get('joined_month', get_joined_month()))
    student_data = {
        'student_id': generate_student_id(),
        'fullname': reg_data['fullname'],
        'email': reg_data['email'],
        'password': reg_data['password'],
        'passed_out_year': reg_data.get('passed_out_year', ''),
        'joined_month': reg_data.get('joined_month', ''),
        'college_name': reg_data.get('college_name', ''),
        'mobile_number': reg_data.get('mobile_number', ''),
        'parent_mobile': reg_data.get('parent_mobile', ''),
        'qualification': reg_data.get('qualification', ''),
        'batch': batch,
        'status': 'pending',
        'login_streak': 0,
        'last_login': '',
        'email_verified': 'true',
        'created_at': datetime.now().isoformat()
    }
    result = db_insert('students', student_data)
    session.pop('reg_otp', None)
    session.pop('reg_otp_time', None)
    session.pop('reg_data', None)
    if result:
        log_student_activity(result.get('id', ''), 'registered', 'Account created, pending approval')
        flash('Registration successful! Your account is pending admin approval.', 'success')
    else:
        flash('Registration failed. Please try again.', 'error')
    return redirect(url_for('index'))

@app.route('/resend-otp')
def resend_otp():
    reg_data = session.get('reg_data', {})
    email = reg_data.get('email', '')
    if not email:
        flash('No registration in progress.', 'error')
        return redirect(url_for('index'))
    otp = generate_otp()
    session['reg_otp'] = otp
    session['reg_otp_time'] = datetime.now().isoformat()
    send_otp_email(email, otp, "email verification")
    flash('New OTP sent to your email.', 'info')
    return redirect(url_for('index'))

# ==================== LOGIN ====================
@app.route('/login', methods=['POST'])
def login():
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '').strip()
    if not all([email, password]):
        flash('Email and password required.', 'error')
        return redirect(url_for('index'))
    students = db_get('students', {'email': email})
    if not students:
        flash('Invalid credentials.', 'error')
        return redirect(url_for('index'))
    student = students[0]
    if not check_password_hash(student.get('password', ''), password):
        flash('Invalid credentials.', 'error')
        return redirect(url_for('index'))
    status = student.get('status', 'pending')
    if status == 'pending':
        flash('Account waiting for admin approval.', 'error')
        return redirect(url_for('index'))
    if status == 'rejected':
        flash('Your account has been rejected.', 'error')
        return redirect(url_for('index'))
    update_login_streak(student)
    session['user_id'] = student.get('id', '')
    session['student_id'] = student.get('student_id', '')
    session['user_name'] = student.get('fullname', '')
    session['user_type'] = 'student'
    session['user_email'] = student.get('email', '')
    session['user_avatar'] = student.get('avatar', '')
    flash('Login successful!', 'success')
    return redirect(url_for('student_dashboard'))

# ==================== PASSWORD MANAGEMENT ====================
@app.route('/forgot-password', methods=['POST'])
def forgot_password():
    email = request.form.get('email', '').strip().lower()
    if not email:
        flash('Please enter your email.', 'error')
        return redirect(url_for('index'))
    students = db_get('students', {'email': email})
    if not students:
        flash('No account found with this email.', 'error')
        return redirect(url_for('index'))
    otp = generate_otp()
    session['reset_email'] = email
    session['reset_otp'] = otp
    session['reset_otp_time'] = datetime.now().isoformat()
    send_otp_email(email, otp, "password reset")
    flash('Password reset OTP sent to your email.', 'info')
    return redirect(url_for('index'))

@app.route('/verify-reset-otp', methods=['POST'])
def verify_reset_otp():
    user_otp = request.form.get('otp', '').strip()
    new_password = request.form.get('new_password', '').strip()
    stored_otp = session.get('reset_otp', '')
    email = session.get('reset_email', '')
    if not user_otp or not new_password:
        flash('OTP and new password required.', 'error')
        return redirect(url_for('index'))
    if user_otp != stored_otp:
        flash('Invalid OTP.', 'error')
        return redirect(url_for('index'))
    if len(new_password) < 6:
        flash('Password must be at least 6 characters.', 'error')
        return redirect(url_for('index'))
    students = db_get('students', {'email': email})
    if not students:
        flash('Account not found.', 'error')
        return redirect(url_for('index'))
    hashed = generate_password_hash(new_password)
    result = db_update('students', students[0]['id'], {'password': hashed})
    session.pop('reset_otp', None)
    session.pop('reset_email', None)
    session.pop('reset_otp_time', None)
    flash(
        'Password reset successfully! Please login.' if result else 'Failed.',
        'success' if result else 'error'
    )
    return redirect(url_for('index'))

@app.route('/change-password', methods=['POST'])
@login_required
def change_password():
    current = request.form.get('current_password', '').strip()
    new_pw = request.form.get('new_password', '').strip()
    if not current or not new_pw:
        flash('Current and new password required.', 'error')
        return redirect(url_for('student_dashboard'))
    if len(new_pw) < 6:
        flash('New password must be at least 6 characters.', 'error')
        return redirect(url_for('student_dashboard'))
    student = db_get_by_id('students', session['user_id'])
    if not student:
        flash('Student not found.', 'error')
        return redirect(url_for('logout'))
    if not check_password_hash(student.get('password', ''), current):
        flash('Current password is incorrect.', 'error')
        return redirect(url_for('student_dashboard'))
    result = db_update('students', session['user_id'], {'password': generate_password_hash(new_pw)})
    flash('Password changed!' if result else 'Failed.', 'success' if result else 'error')
    return redirect(url_for('student_dashboard'))

# ==================== ADMIN LOGIN ====================
@app.route('/admin-login', methods=['POST'])
def admin_login():
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '').strip()
    admin_email = os.getenv('ADMIN_EMAIL', 'admin@gmail.com')
    admin_password = os.getenv('ADMIN_PASSWORD', '123')
    if email == admin_email and password == admin_password:
        admin_details = get_admin_details(email)
        if admin_details:
            session['admin_name'] = admin_details.get('name', 'Administrator')
            session['admin_avatar'] = admin_details.get('avatar', '')
            session['admin_object_id'] = admin_details.get('id', '')
        else:
            new_admin = create_or_update_admin_details(email, 'Administrator', '')
            if new_admin:
                session['admin_name'] = new_admin.get('name', 'Administrator')
                session['admin_avatar'] = new_admin.get('avatar', '')
                session['admin_object_id'] = new_admin.get('id', '')
            else:
                session['admin_name'] = 'Administrator'
                session['admin_avatar'] = ''
                session['admin_object_id'] = ''
        session['admin_id'] = 'admin'
        session['user_type'] = 'admin'
        session['admin_email'] = email
        flash('Admin login successful!', 'success')
        return redirect(url_for('admin_dashboard'))
    else:
        flash('Invalid admin credentials.', 'error')
        return redirect(url_for('index'))

# ==================== DEMO LOGIN ====================
@app.route('/demo-login')
def demo_login():
    session['user_id'] = 'demo_user'
    session['student_id'] = 'DEMO0001'
    session['user_name'] = 'Demo Student'
    session['user_type'] = 'demo'
    flash('Welcome to Demo Portal! Explore limited content.', 'info')
    return redirect(url_for('demo_dashboard'))

# ==================== LOGOUT ====================
@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('index'))

# ==================== STUDENT DASHBOARD ====================
@app.route('/student-dashboard')
@login_required
def student_dashboard():
    if session.get('user_type') != 'student':
        flash('Access denied.', 'error')
        return redirect(url_for('index'))
    student = db_get_by_id('students', session['user_id'])
    if not student:
        flash('Student not found.', 'error')
        return redirect(url_for('logout'))
    student_batch = student.get('batch', '')
    all_announcements = db_get('announcements') or []
    announcements = [a for a in all_announcements if a.get('batch', 'all') in ['all', student_batch]]
    folders = db_get('folders') or []
    all_videos = db_get('videos') or []
    videos = [v for v in all_videos]
    return render_template('user.html',
                           student=student,
                           announcements=announcements,
                           folders=folders,
                           videos=videos)

# ==================== STUDENT PROFILE UPDATE ====================
@app.route('/update-profile', methods=['POST'])
@login_required
def update_profile():
    fullname = request.form.get('fullname', '').strip()
    data = {
        'fullname': fullname,
        'mobile_number': request.form.get('mobile_number', '').strip(),
        'parent_mobile': request.form.get('parent_mobile', '').strip(),
        'college_name': request.form.get('college_name', '').strip(),
        'qualification': request.form.get('qualification', '').strip(),
        'passed_out_year': request.form.get('passed_out_year', '').strip()
    }
    if not fullname:
        flash('Name is required.', 'error')
        return redirect(url_for('student_dashboard'))
    result = db_update('students', session['user_id'], data)
    if result:
        session['user_name'] = fullname
        log_student_activity(session['user_id'], 'profile_update', 'Profile updated')
        flash('Profile updated successfully!', 'success')
    else:
        flash('Failed to update profile.', 'error')
    return redirect(url_for('student_dashboard'))

@app.route('/student/upload-avatar', methods=['POST'])
@login_required
def student_upload_avatar():
    avatar_file = request.files.get('avatar')
    if avatar_file and avatar_file.filename:
        allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
        file_ext = avatar_file.filename.rsplit('.', 1)[1].lower() if '.' in avatar_file.filename else ''
        if file_ext not in allowed_extensions:
            flash('Only image files are allowed (PNG, JPG, JPEG, GIF, WEBP).', 'error')
            return redirect(url_for('student_dashboard'))
        
        # Upload to Supabase Storage
        avatar_url = upload_file('student_avatars', avatar_file, avatar_file.filename)
        if avatar_url:
            # Delete old avatar if exists
            student = db_get_by_id('students', session['user_id'])
            if student:
                old_avatar = student.get('avatar')
                if old_avatar:
                    delete_storage_file(old_avatar)
            
            result = db_update('students', session['user_id'], {'avatar': avatar_url})
            if result:
                session['user_avatar'] = avatar_url
                flash('Profile photo updated successfully!', 'success')
            else:
                flash('Failed to save photo.', 'error')
        else:
            flash('Failed to upload photo.', 'error')
    else:
        flash('Please select a file.', 'error')
    return redirect(url_for('student_dashboard'))

# ==================== DEMO DASHBOARD ====================
@app.route('/demo-dashboard')
def demo_dashboard():
    if session.get('user_type') != 'demo':
        flash('Please access demo first.', 'error')
        return redirect(url_for('index'))

    student = {
        'student_id': 'DEMO0001',
        'fullname': 'Demo Student',
        'email': 'demo@slokamtechnology.com',
        'batch': 'Demo Access',
        'joined_month': datetime.now().strftime('%B %Y'),
        'login_streak': 0,
        'status': 'demo',
        'college_name': '-',
        'mobile_number': '-',
        'qualification': '-',
        'passed_out_year': '-'
    }

    all_announcements = db_get('announcements') or []
    announcements = [a for a in all_announcements if a.get('batch', 'all') == 'all']

    folders = db_get('folders') or []
    all_videos = db_get('videos') or []
    demo_videos = [v for v in all_videos if is_demo_true(v.get('is_demo', False))]
    print(f"Demo videos found: {len(demo_videos)}")

    demo_folder_ids = set(v.get('folder_id', '') for v in demo_videos if v.get('folder_id'))
    demo_folders = [f for f in folders if f.get('id') in demo_folder_ids]
    print(f"Demo folders found: {len(demo_folders)}")

    return render_template('demo.html',
                           student=student,
                           announcements=announcements,
                           folders=demo_folders,
                           videos=demo_videos)

# ==================== ADMIN DASHBOARD ====================
@app.route('/admin-dashboard')
@admin_required
def admin_dashboard():
    admin_details = get_admin_details(session.get('admin_email', ''))
    if admin_details:
        session['admin_name'] = admin_details.get('name', session.get('admin_name', 'Administrator'))
        session['admin_avatar'] = admin_details.get('avatar', session.get('admin_avatar', ''))
    students = db_get('students') or []
    approved = [s for s in students if s.get('status') == 'approved']
    pending = [s for s in students if s.get('status') == 'pending']
    rejected = [s for s in students if s.get('status') == 'rejected']
    batches_set = sorted(set(s.get('batch', '') for s in students if s.get('batch')))
    folders = db_get('folders') or []
    videos = db_get('videos') or []
    announcements = db_get('announcements') or []
    total_streak = sum(safe_int(s.get('login_streak'), 0) for s in approved)
    avg_streak = round(total_streak / len(approved), 2) if approved else 0
    batch_activity = {}
    for s in approved:
        batch = s.get('batch', 'Unknown')
        streak = safe_int(s.get('login_streak'), 0)
        if batch not in batch_activity:
            batch_activity[batch] = {'count': 0, 'total_streak': 0}
        batch_activity[batch]['count'] += 1
        batch_activity[batch]['total_streak'] += streak
    most_active_batch = 'N/A'
    highest_avg = 0
    for batch, data in batch_activity.items():
        avg = data['total_streak'] / data['count'] if data['count'] > 0 else 0
        if avg > highest_avg:
            highest_avg = avg
            most_active_batch = batch
    student_activities = sorted(db_get('student_activities') or [],
                                key=lambda x: safe_str(x.get('created_at', '')), reverse=True)[:10]
    for act in student_activities:
        s = db_get_by_id('students', act.get('student_object_id', ''))
        act['student_name'] = s.get('fullname', 'Unknown') if s else 'Unknown'
        act['student_id_display'] = s.get('student_id', '') if s else ''
    recent_students = sorted(students, key=lambda x: safe_str(x.get('created_at', '')), reverse=True)[:5]
    return render_template('admin.html',
                           active_tab='dashboard',
                           admin_name=session.get('admin_name', 'Administrator'),
                           admin_email=session.get('admin_email', ''),
                           admin_avatar=session.get('admin_avatar', ''),
                           total_students=len(students),
                           approved_students=len(approved),
                           pending_students=len(pending),
                           rejected_students=len(rejected),
                           total_batches=len(batches_set),
                           total_folders=len(folders),
                           total_videos=len(videos),
                           total_announcements=len(announcements),
                           average_streak=avg_streak,
                           most_active_batch=most_active_batch,
                           students=students,
                           batches=batches_set,
                           batch_data=batch_activity,
                           recent_students=recent_students,
                           all_activities=student_activities,
                           folders=folders,
                           videos=videos,
                           announcements=announcements,
                           student_activities=student_activities)

# ==================== ADMIN STUDENT MANAGEMENT ====================
@app.route('/admin/students')
@admin_required
def admin_students():
    search = request.args.get('search', '').strip()
    batch_filter = request.args.get('batch', '').strip()
    status_filter = request.args.get('status', '').strip()
    all_students = db_get('students') or []
    students = all_students.copy()
    if search:
        sl = search.lower()
        students = [s for s in students if sl in safe_str(s.get('fullname', '')).lower()
                    or sl in safe_str(s.get('student_id', '')).lower()
                    or sl in safe_str(s.get('email', '')).lower()]
    if batch_filter:
        students = [s for s in students if s.get('batch') == batch_filter]
    if status_filter:
        students = [s for s in students if s.get('status') == status_filter]
    batches = sorted(set(s.get('batch', '') for s in all_students if s.get('batch')))
    approved = [s for s in all_students if s.get('status') == 'approved']
    pending = [s for s in all_students if s.get('status') == 'pending']
    rejected = [s for s in all_students if s.get('status') == 'rejected']
    total_folders = len(db_get('folders') or [])
    total_videos = len(db_get('videos') or [])
    total_announcements = len(db_get('announcements') or [])
    return render_template('admin.html',
                           active_tab='students',
                           admin_name=session.get('admin_name', 'Administrator'),
                           admin_email=session.get('admin_email', ''),
                           admin_avatar=session.get('admin_avatar', ''),
                           students=students,
                           all_students=all_students,
                           batches=batches,
                           total_students=len(all_students),
                           approved_students=len(approved),
                           pending_students=len(pending),
                           rejected_students=len(rejected),
                           total_batches=len(batches),
                           total_folders=total_folders,
                           total_videos=total_videos,
                           total_announcements=total_announcements,
                           search=search,
                           batch_filter=batch_filter,
                           status_filter=status_filter,
                           folders=db_get('folders') or [],
                           videos=db_get('videos') or [],
                           announcements=db_get('announcements') or [],
                           student_activities=[],
                           average_streak=0,
                           most_active_batch='N/A')

@app.route('/admin/student/<object_id>')
@admin_required
def admin_student_detail(object_id):
    student = db_get_by_id('students', object_id)
    if not student:
        flash('Student not found.', 'error')
        return redirect(url_for('admin_students'))
    all_students = db_get('students') or []
    batches = sorted(set(s.get('batch', '') for s in all_students if s.get('batch')))
    pending = [s for s in all_students if s.get('status') == 'pending']
    total_folders = len(db_get('folders') or [])
    total_videos = len(db_get('videos') or [])
    total_announcements = len(db_get('announcements') or [])
    all_activities = db_get('student_activities') or []
    student_activities = [a for a in all_activities if a.get('student_object_id') == object_id]
    student_activities = sorted(student_activities, key=lambda x: safe_str(x.get('created_at', '')), reverse=True)[:20]
    return render_template('admin.html',
                           active_tab='students',
                           admin_name=session.get('admin_name', 'Administrator'),
                           admin_email=session.get('admin_email', ''),
                           admin_avatar=session.get('admin_avatar', ''),
                           view_student=student,
                           students=all_students,
                           batches=batches,
                           total_students=len(all_students),
                           pending_students=len(pending),
                           approved_students=len([s for s in all_students if s.get('status') == 'approved']),
                           rejected_students=len([s for s in all_students if s.get('status') == 'rejected']),
                           total_batches=len(batches),
                           total_folders=total_folders,
                           total_videos=total_videos,
                           total_announcements=total_announcements,
                           student_activities=student_activities,
                           folders=db_get('folders') or [],
                           videos=db_get('videos') or [],
                           announcements=db_get('announcements') or [],
                           average_streak=0,
                           most_active_batch='N/A')

@app.route('/admin/student/edit/<object_id>', methods=['POST'])
@admin_required
def admin_edit_student(object_id):
    fullname = request.form.get('fullname', '').strip()
    email = request.form.get('email', '').strip().lower()
    joined_month = request.form.get('joined_month', '').strip()
    status = request.form.get('status', '').strip()
    if not fullname or not email:
        flash('Name and email required.', 'error')
        return redirect(url_for('admin_students'))
    batch = get_batch_from_joined_month(joined_month) if joined_month else None
    data = {'fullname': fullname, 'email': email, 'status': status}
    if joined_month:
        data['joined_month'] = joined_month
    if batch:
        data['batch'] = batch
    result = db_update('students', object_id, data)
    if result:
        log_student_activity(object_id, 'profile_update', f'Admin updated profile. Batch: {batch}')
        flash('Student updated successfully.', 'success')
    else:
        flash('Failed to update student.', 'error')
    return redirect(url_for('admin_students'))

@app.route('/admin/student/delete/<object_id>')
@admin_required
def admin_delete_student(object_id):
    # Delete student avatar if exists
    student = db_get_by_id('students', object_id)
    if student:
        old_avatar = student.get('avatar')
        if old_avatar:
            delete_storage_file(old_avatar)
    
    result = db_delete('students', object_id)
    flash(
        'Student deleted successfully.' if result else 'Failed to delete student.',
        'success' if result else 'error'
    )
    return redirect(url_for('admin_students'))

@app.route('/approve/<object_id>')
@admin_required
def approve_student(object_id):
    if not object_id or object_id == '':
        flash('Invalid student ID.', 'error')
        return redirect(url_for('admin_students'))
    result = db_update('students', object_id, {'status': 'approved'})
    if result:
        log_student_activity(object_id, 'approved', 'Account approved by admin')
        flash('Student approved successfully!', 'success')
    else:
        flash('Failed to approve student.', 'error')
    return redirect(url_for('admin_students'))

@app.route('/reject/<object_id>')
@admin_required
def reject_student(object_id):
    if not object_id or object_id == '':
        flash('Invalid student ID.', 'error')
        return redirect(url_for('admin_students'))
    
    # Delete student avatar if exists before deletion
    student = db_get_by_id('students', object_id)
    if student:
        old_avatar = student.get('avatar')
        if old_avatar:
            delete_storage_file(old_avatar)
    
    result = db_delete('students', object_id)
    flash(
        'Student rejected and removed.' if result else 'Failed to reject student.',
        'success' if result else 'error'
    )
    return redirect(url_for('admin_students'))

# ==================== ADMIN BATCH MANAGEMENT ====================
@app.route('/admin/batches')
@admin_required
def admin_batches():
    students = db_get('students') or []
    batch_data = {}
    pending = [s for s in students if s.get('status') == 'pending']
    approved = [s for s in students if s.get('status') == 'approved']
    rejected = [s for s in students if s.get('status') == 'rejected']
    for s in students:
        batch = s.get('batch', 'Unknown') or 'Unknown'
        if batch not in batch_data:
            batch_data[batch] = {'students': [], 'approved': 0, 'pending': 0, 'avg_streak': 0}
        batch_data[batch]['students'].append(s)
        if s.get('status') == 'approved':
            batch_data[batch]['approved'] += 1
        elif s.get('status') == 'pending':
            batch_data[batch]['pending'] += 1
    for batch, data in batch_data.items():
        streaks = [safe_int(s.get('login_streak'), 0) for s in data['students'] if s.get('status') == 'approved']
        data['avg_streak'] = round(sum(streaks) / len(streaks), 1) if streaks else 0
    batches_list = sorted(batch_data.keys())
    total_batches = len(batches_list)
    total_folders = len(db_get('folders') or [])
    total_videos = len(db_get('videos') or [])
    total_announcements = len(db_get('announcements') or [])
    return render_template('admin.html',
                           active_tab='batches',
                           admin_name=session.get('admin_name', 'Administrator'),
                           admin_email=session.get('admin_email', ''),
                           admin_avatar=session.get('admin_avatar', ''),
                           batch_data=batch_data,
                           batches=batches_list,
                           total_batches=total_batches,
                           students=students,
                           pending_students=len(pending),
                           approved_students=len(approved),
                           rejected_students=len(rejected),
                           total_students=len(students),
                           total_folders=total_folders,
                           total_videos=total_videos,
                           total_announcements=total_announcements,
                           folders=db_get('folders') or [],
                           videos=db_get('videos') or [],
                           announcements=db_get('announcements') or [],
                           student_activities=[],
                           average_streak=0,
                           most_active_batch='N/A')

# ==================== ADMIN FOLDER MANAGEMENT ====================
@app.route('/admin/folders')
@admin_required
def admin_folders():
    folders = db_get('folders') or []
    videos = db_get('videos') or []
    students = db_get('students') or []
    batches = sorted(set(s.get('batch', '') for s in students if s.get('batch')))
    pending = [s for s in students if s.get('status') == 'pending']
    approved = [s for s in students if s.get('status') == 'approved']
    rejected = [s for s in students if s.get('status') == 'rejected']
    total_folders = len(folders)
    total_videos = len(videos)
    total_announcements = len(db_get('announcements') or [])
    return render_template('admin.html',
                           active_tab='folders',
                           admin_name=session.get('admin_name', 'Administrator'),
                           admin_email=session.get('admin_email', ''),
                           admin_avatar=session.get('admin_avatar', ''),
                           folders=folders,
                           videos=videos,
                           total_folders=total_folders,
                           total_videos=total_videos,
                           pending_students=len(pending),
                           approved_students=len(approved),
                           rejected_students=len(rejected),
                           total_students=len(students),
                           total_batches=len(batches),
                           total_announcements=total_announcements,
                           students=students,
                           batches=batches,
                           announcements=db_get('announcements') or [],
                           student_activities=[],
                           average_streak=0,
                           most_active_batch='N/A')

@app.route('/admin/add-folder', methods=['POST'])
@admin_required
def add_folder():
    folder_name = request.form.get('folder_name', '').strip()
    folder_date = request.form.get('folder_date', '').strip()
    if not folder_name:
        flash('Folder name is required.', 'error')
        return redirect(url_for('admin_folders'))
    data = {
        'folder_name': folder_name,
        'folder_date': folder_date if folder_date else datetime.now().strftime('%Y-%m-%d'),
        'created_at': datetime.now().isoformat()
    }
    result = db_insert('folders', data)
    flash('Folder added successfully.' if result else 'Failed to add folder.', 'success' if result else 'error')
    return redirect(url_for('admin_folders'))

@app.route('/admin/edit-folder/<object_id>', methods=['POST'])
@admin_required
def edit_folder(object_id):
    folder_name = request.form.get('folder_name', '').strip()
    folder_date = request.form.get('folder_date', '').strip()
    if not folder_name:
        flash('Folder name is required.', 'error')
        return redirect(url_for('admin_folders'))
    data = {
        'folder_name': folder_name,
        'folder_date': folder_date if folder_date else datetime.now().strftime('%Y-%m-%d')
    }
    result = db_update('folders', object_id, data)
    flash('Folder updated successfully.' if result else 'Failed to update folder.', 'success' if result else 'error')
    return redirect(url_for('admin_folders'))

@app.route('/admin/delete-folder/<object_id>')
@admin_required
def delete_folder(object_id):
    result = db_delete('folders', object_id)
    flash('Folder deleted successfully.' if result else 'Failed to delete folder.', 'success' if result else 'error')
    return redirect(url_for('admin_folders'))

# ==================== ADMIN VIDEO MANAGEMENT ====================
@app.route('/admin/videos')
@admin_required
def admin_videos():
    videos = db_get('videos') or []
    folders = db_get('folders') or []
    students = db_get('students') or []
    batches = sorted(set(s.get('batch', '') for s in students if s.get('batch')))
    pending = [s for s in students if s.get('status') == 'pending']
    approved = [s for s in students if s.get('status') == 'approved']
    rejected = [s for s in students if s.get('status') == 'rejected']
    total_videos = len(videos)
    total_folders = len(folders)
    total_announcements = len(db_get('announcements') or [])
    return render_template('admin.html',
                           active_tab='videos',
                           admin_name=session.get('admin_name', 'Administrator'),
                           admin_email=session.get('admin_email', ''),
                           admin_avatar=session.get('admin_avatar', ''),
                           videos=videos,
                           folders=folders,
                           total_videos=total_videos,
                           pending_students=len(pending),
                           approved_students=len(approved),
                           rejected_students=len(rejected),
                           total_students=len(students),
                           total_batches=len(batches),
                           total_folders=total_folders,
                           total_announcements=total_announcements,
                           students=students,
                           batches=batches,
                           announcements=db_get('announcements') or [],
                           student_activities=[],
                           average_streak=0,
                           most_active_batch='N/A')

@app.route('/admin/add-video', methods=['POST'])
@admin_required
def add_video():
    title = request.form.get('title', '').strip()
    youtube_link = request.form.get('youtube_link', '').strip()
    video_date = request.form.get('video_date', '').strip()
    folder_id = request.form.get('folder_id', '').strip()
    description = request.form.get('description', '').strip()
    is_demo = request.form.get('is_demo', 'false').strip()
    notes_file = request.files.get('notes_file')
    notes_link = request.form.get('notes_link', '').strip()
    exercise_file = request.files.get('exercise_file')
    exercise_link = request.form.get('exercise_link', '').strip()
    practice_file = request.files.get('practice_file')
    practice_link = request.form.get('practice_link', '').strip()
    
    if not all([title, youtube_link, folder_id]):
        flash('Title, YouTube link, and folder are required.', 'error')
        return redirect(url_for('admin_videos'))
    
    folder = db_get_by_id('folders', folder_id)
    folder_name = folder.get('folder_name', 'Unknown') if folder else 'Unknown'
    
    # Upload files to Supabase Storage
    notes_url = notes_link
    if notes_file and notes_file.filename:
        notes_url = upload_file('notes', notes_file, notes_file.filename)
    
    exercise_url = exercise_link
    if exercise_file and exercise_file.filename:
        exercise_url = upload_file('exercises', exercise_file, exercise_file.filename)
    
    practice_url = practice_link
    if practice_file and practice_file.filename:
        practice_url = upload_file('practice', practice_file, practice_file.filename)
    
    is_demo_value = 'true' if is_demo in ['true', '1', 'on', 'yes'] else 'false'
    data = {
        'title': title,
        'youtube_link': youtube_link,
        'video_date': video_date if video_date else datetime.now().strftime('%Y-%m-%d'),
        'folder_id': folder_id,
        'folder_name': folder_name,
        'description': description,
        'notes_file': notes_url if notes_url else '',
        'exercise_file': exercise_url if exercise_url else '',
        'practice_file': practice_url if practice_url else '',
        'is_demo': is_demo_value,
        'created_at': datetime.now().isoformat()
    }
    result = db_insert('videos', data)
    flash('Video added successfully.' if result else 'Failed to add video.', 'success' if result else 'error')
    return redirect(url_for('admin_videos'))

@app.route('/admin/edit-video/<object_id>', methods=['POST'])
@admin_required
def edit_video(object_id):
    title = request.form.get('title', '').strip()
    youtube_link = request.form.get('youtube_link', '').strip()
    video_date = request.form.get('video_date', '').strip()
    folder_id = request.form.get('folder_id', '').strip()
    description = request.form.get('description', '').strip()
    is_demo = request.form.get('is_demo', 'false').strip()
    notes_file = request.files.get('notes_file')
    notes_link = request.form.get('notes_link', '').strip()
    exercise_file = request.files.get('exercise_file')
    exercise_link = request.form.get('exercise_link', '').strip()
    practice_file = request.files.get('practice_file')
    practice_link = request.form.get('practice_link', '').strip()
    
    if not all([title, youtube_link, folder_id]):
        flash('All fields required.', 'error')
        return redirect(url_for('admin_videos'))
    
    folder = db_get_by_id('folders', folder_id)
    
    # Get existing video to delete old files
    existing_video = db_get_by_id('videos', object_id)
    
    data = {
        'title': title,
        'youtube_link': youtube_link,
        'video_date': video_date if video_date else datetime.now().strftime('%Y-%m-%d'),
        'folder_id': folder_id,
        'folder_name': folder.get('folder_name', 'Unknown') if folder else 'Unknown',
        'description': description,
        'is_demo': 'true' if is_demo in ['true', '1', 'on', 'yes'] else 'false'
    }
    
    # Handle notes file
    if notes_link:
        data['notes_file'] = notes_link
        # Delete old file if exists
        if existing_video and existing_video.get('notes_file'):
            delete_storage_file(existing_video.get('notes_file'))
    elif notes_file and notes_file.filename:
        # Delete old file if exists
        if existing_video and existing_video.get('notes_file'):
            delete_storage_file(existing_video.get('notes_file'))
        notes_url = upload_file('notes', notes_file, notes_file.filename)
        if notes_url:
            data['notes_file'] = notes_url
    elif existing_video and existing_video.get('notes_file') and not notes_link and not notes_file:
        # Keep existing file
        data['notes_file'] = existing_video.get('notes_file')
    
    # Handle exercise file
    if exercise_link:
        data['exercise_file'] = exercise_link
        # Delete old file if exists
        if existing_video and existing_video.get('exercise_file'):
            delete_storage_file(existing_video.get('exercise_file'))
    elif exercise_file and exercise_file.filename:
        # Delete old file if exists
        if existing_video and existing_video.get('exercise_file'):
            delete_storage_file(existing_video.get('exercise_file'))
        exercise_url = upload_file('exercises', exercise_file, exercise_file.filename)
        if exercise_url:
            data['exercise_file'] = exercise_url
    elif existing_video and existing_video.get('exercise_file') and not exercise_link and not exercise_file:
        # Keep existing file
        data['exercise_file'] = existing_video.get('exercise_file')
    
    # Handle practice file
    if practice_link:
        data['practice_file'] = practice_link
        # Delete old file if exists
        if existing_video and existing_video.get('practice_file'):
            delete_storage_file(existing_video.get('practice_file'))
    elif practice_file and practice_file.filename:
        # Delete old file if exists
        if existing_video and existing_video.get('practice_file'):
            delete_storage_file(existing_video.get('practice_file'))
        practice_url = upload_file('practice', practice_file, practice_file.filename)
        if practice_url:
            data['practice_file'] = practice_url
    elif existing_video and existing_video.get('practice_file') and not practice_link and not practice_file:
        # Keep existing file
        data['practice_file'] = existing_video.get('practice_file')
    
    result = db_update('videos', object_id, data)
    flash('Video updated successfully.' if result else 'Failed to update video.', 'success' if result else 'error')
    return redirect(url_for('admin_videos'))

@app.route('/admin/delete-video/<object_id>')
@admin_required
def delete_video(object_id):
    video = db_get_by_id('videos', object_id)
    if video:
        # Delete all associated files from Supabase Storage
        for field in ['notes_file', 'exercise_file', 'practice_file']:
            file_url = video.get(field)
            if file_url:
                delete_storage_file(file_url)
    
    result = db_delete('videos', object_id)
    flash('Video deleted successfully.' if result else 'Failed to delete video.', 'success' if result else 'error')
    return redirect(url_for('admin_videos'))

# ==================== ADMIN ANNOUNCEMENT MANAGEMENT ====================
@app.route('/admin/announcements')
@admin_required
def admin_announcements():
    announcements = db_get('announcements') or []
    students = db_get('students') or []
    batches = sorted(set(s.get('batch', '') for s in students if s.get('batch')))
    pending = [s for s in students if s.get('status') == 'pending']
    approved = [s for s in students if s.get('status') == 'approved']
    rejected = [s for s in students if s.get('status') == 'rejected']
    total_announcements = len(announcements)
    total_folders = len(db_get('folders') or [])
    total_videos = len(db_get('videos') or [])
    return render_template('admin.html',
                           active_tab='announcements',
                           admin_name=session.get('admin_name', 'Administrator'),
                           admin_email=session.get('admin_email', ''),
                           admin_avatar=session.get('admin_avatar', ''),
                           announcements=announcements,
                           batches=batches,
                           total_announcements=total_announcements,
                           pending_students=len(pending),
                           approved_students=len(approved),
                           rejected_students=len(rejected),
                           total_students=len(students),
                           total_batches=len(batches),
                           total_folders=total_folders,
                           total_videos=total_videos,
                           students=students,
                           folders=db_get('folders') or [],
                           videos=db_get('videos') or [],
                           student_activities=[],
                           average_streak=0,
                           most_active_batch='N/A')

@app.route('/admin/add-announcement', methods=['POST'])
@admin_required
def add_announcement():
    title = request.form.get('title', '').strip()
    message = request.form.get('message', '').strip()
    target_batch = request.form.get('target_batch', 'all').strip()
    attachment_url = ''
    attachment_name = ''
    attachment_type = ''
    attachment_file = request.files.get('attachment')
    
    if attachment_file and attachment_file.filename:
        uploaded_url = upload_file('announcements', attachment_file, attachment_file.filename)
        if uploaded_url:
            attachment_url = uploaded_url
            attachment_name = attachment_file.filename
            attachment_type = attachment_file.filename.rsplit('.', 1)[-1].lower() if '.' in attachment_file.filename else 'file'
    
    link_url = request.form.get('link_url', '').strip()
    link_text = request.form.get('link_text', '').strip()
    
    if not all([title, message]):
        flash('Title and message are required.', 'error')
        return redirect(url_for('admin_announcements'))
    
    data = {
        'title': title,
        'message': message,
        'batch': target_batch,
        'created_at': datetime.now().isoformat(),
        'admin_name': session.get('admin_name', 'Administrator'),
        'admin_avatar': session.get('admin_avatar', ''),
        'attachment_url': attachment_url,
        'attachment_name': attachment_name,
        'attachment_type': attachment_type,
        'link_url': link_url,
        'link_text': link_text if link_text else 'Click here'
    }
    result = db_insert('announcements', data)
    if result:
        flash('Announcement sent successfully!', 'success')
    else:
        flash('Failed to add announcement.', 'error')
    return redirect(url_for('admin_announcements'))

@app.route('/admin/edit-announcement/<object_id>', methods=['POST'])
@admin_required
def edit_announcement(object_id):
    title = request.form.get('title', '').strip()
    message = request.form.get('message', '').strip()
    target_batch = request.form.get('target_batch', 'all').strip()
    
    # Get existing announcement
    existing_announcement = db_get_by_id('announcements', object_id)
    
    # Handle attachment update
    attachment_file = request.files.get('attachment')
    attachment_url = existing_announcement.get('attachment_url', '') if existing_announcement else ''
    attachment_name = existing_announcement.get('attachment_name', '') if existing_announcement else ''
    attachment_type = existing_announcement.get('attachment_type', '') if existing_announcement else ''
    
    if attachment_file and attachment_file.filename:
        # Delete old attachment if exists
        if existing_announcement and existing_announcement.get('attachment_url'):
            delete_storage_file(existing_announcement.get('attachment_url'))
        
        # Upload new attachment
        uploaded_url = upload_file('announcements', attachment_file, attachment_file.filename)
        if uploaded_url:
            attachment_url = uploaded_url
            attachment_name = attachment_file.filename
            attachment_type = attachment_file.filename.rsplit('.', 1)[-1].lower() if '.' in attachment_file.filename else 'file'
    
    link_url = request.form.get('link_url', '').strip()
    link_text = request.form.get('link_text', '').strip()
    
    if not all([title, message]):
        flash('Title and message required.', 'error')
        return redirect(url_for('admin_announcements'))
    
    data = {
        'title': title, 
        'message': message, 
        'batch': target_batch,
        'attachment_url': attachment_url,
        'attachment_name': attachment_name,
        'attachment_type': attachment_type,
        'link_url': link_url,
        'link_text': link_text if link_text else 'Click here'
    }
    
    result = db_update('announcements', object_id, data)
    flash(
        'Announcement updated successfully.' if result else 'Failed to update.',
        'success' if result else 'error'
    )
    return redirect(url_for('admin_announcements'))

@app.route('/admin/delete-announcement/<object_id>')
@admin_required
def delete_announcement(object_id):
    announcement = db_get_by_id('announcements', object_id)
    if announcement and announcement.get('attachment_url'):
        # Delete attachment from Supabase Storage
        if announcement.get('attachment_url'):
            delete_storage_file(announcement.get('attachment_url'))
    
    result = db_delete('announcements', object_id)
    flash(
        'Announcement deleted successfully.' if result else 'Failed to delete.',
        'success' if result else 'error'
    )
    return redirect(url_for('admin_announcements'))

# ==================== ADMIN ANALYTICS ====================
@app.route('/admin/analytics')
@admin_required
def admin_analytics():
    students = db_get('students') or []
    approved = [s for s in students if s.get('status') == 'approved']
    pending = [s for s in students if s.get('status') == 'pending']
    rejected = [s for s in students if s.get('status') == 'rejected']
    batches_set = sorted(set(s.get('batch', 'Unknown') for s in students))
    folders = db_get('folders') or []
    videos = db_get('videos') or []
    announcements = db_get('announcements') or []
    total_streak = sum(safe_int(s.get('login_streak'), 0) for s in approved)
    avg_streak = round(total_streak / len(approved), 2) if approved else 0
    batch_activity = {}
    for s in approved:
        b = s.get('batch', 'Unknown')
        if b not in batch_activity:
            batch_activity[b] = {'count': 0, 'total_streak': 0}
        batch_activity[b]['count'] += 1
        batch_activity[b]['total_streak'] += safe_int(s.get('login_streak'), 0)
    most_active = (
        max(
            batch_activity,
            key=lambda x: batch_activity[x]['total_streak'] / batch_activity[x]['count']
            if batch_activity[x]['count'] > 0 else 0
        )
        if batch_activity else 'N/A'
    )
    all_activities = sorted(db_get('student_activities') or [], key=lambda x: safe_str(x.get('created_at', '')), reverse=True)[:20]
    for act in all_activities:
        s = db_get_by_id('students', act.get('student_object_id', ''))
        act['student_name'] = s.get('fullname', 'Unknown') if s else 'Unknown'
        act['student_id_display'] = s.get('student_id', '') if s else ''
    recent_students = sorted(students, key=lambda x: safe_str(x.get('created_at', '')), reverse=True)[:5]
    return render_template('admin.html',
                           active_tab='analytics',
                           admin_name=session.get('admin_name', 'Administrator'),
                           admin_email=session.get('admin_email', ''),
                           admin_avatar=session.get('admin_avatar', ''),
                           total_students=len(students),
                           approved_students=len(approved),
                           pending_students=len(pending),
                           rejected_students=len(rejected),
                           total_batches=len(batches_set),
                           total_folders=len(folders),
                           total_videos=len(videos),
                           total_announcements=len(announcements),
                           average_streak=avg_streak,
                           most_active_batch=most_active,
                           students=students,
                           batches=batches_set,
                           batch_activity=batch_activity,
                           recent_students=recent_students,
                           all_activities=all_activities,
                           folders=folders,
                           videos=videos,
                           announcements=announcements,
                           student_activities=[])

# ==================== ADMIN PROFILE ====================
@app.route('/admin/upload-avatar', methods=['POST'])
@admin_required
def admin_upload_avatar():
    avatar_file = request.files.get('avatar')
    if avatar_file and avatar_file.filename:
        allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
        file_ext = avatar_file.filename.rsplit('.', 1)[1].lower() if '.' in avatar_file.filename else ''
        if file_ext not in allowed_extensions:
            flash('Only image files are allowed (PNG, JPG, JPEG, GIF, WEBP).', 'error')
            return redirect(url_for('admin_dashboard'))
        
        # Upload to Supabase Storage
        avatar_url = upload_file('avatars', avatar_file, avatar_file.filename)
        if avatar_url:
            # Delete old avatar if exists
            admin_details = get_admin_details(session.get('admin_email', ''))
            if admin_details:
                old_avatar = admin_details.get('avatar')
                if old_avatar:
                    delete_storage_file(old_avatar)
            
            session['admin_avatar'] = avatar_url
            result = create_or_update_admin_details(session.get('admin_email', ''), admin_avatar=avatar_url)
            if result:
                flash('Profile photo updated successfully!', 'success')
            else:
                flash('Photo uploaded but failed to save to database.', 'warning')
        else:
            flash('Failed to upload photo.', 'error')
    else:
        flash('Please select a file.', 'error')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/update-profile', methods=['POST'])
@admin_required
def admin_update_profile():
    name = request.form.get('admin_name', '').strip()
    if name:
        session['admin_name'] = name
        result = create_or_update_admin_details(session.get('admin_email', ''), admin_name=name)
        if result:
            flash('Profile updated successfully!', 'success')
        else:
            flash('Failed to update profile.', 'error')
    else:
        flash('Name cannot be empty.', 'error')
    return redirect(url_for('admin_dashboard'))

# ==================== ERROR HANDLERS ====================
@app.errorhandler(404)
def not_found_error(error):
    return render_template('base.html'), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('base.html'), 500

# ==================== MAIN ====================
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
