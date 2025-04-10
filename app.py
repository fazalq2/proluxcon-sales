import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from functools import wraps
from passlib.hash import bcrypt
from datetime import datetime
from flask import request, jsonify
from flask_login import login_required, current_user
import logging
import traceback
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

# Set up logging - add this near the top of your file after imports
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


# -----------------------------------------------------------
# Single-File Configuration
# -----------------------------------------------------------
class Config:
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "secret-key")
    SQLALCHEMY_TRACK_MODIFICATIONS = False


class DevelopmentConfig(Config):
    # Use SQLite for development
    SQLALCHEMY_DATABASE_URI = "sqlite:///prolux.db"


class ProductionConfig(Config):
    # Use Postgres in production (must set DATABASE_URL in your env)
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")


# -----------------------------------------------------------
# Initialize Flask & Load Config
# -----------------------------------------------------------
app = Flask(__name__, static_folder="static")


# Flask app configuration
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
    days=1
)  # Set session to last 1 day


@app.before_request
def make_session_permanent():
    session.permanent = True


# Determine the environment (development vs production)
env = os.environ.get("FLASK_ENV", "development")
if env == "production":
    app.config.from_object(ProductionConfig)
else:
    app.config.from_object(DevelopmentConfig)

db = SQLAlchemy(app)


# -----------------------------------------------------------
# App Error Handling
# -----------------------------------------------------------
@app.errorhandler(Exception)
def handle_exception(e):
    """Return JSON instead of HTML for any other error (e.g. 500)"""
    # Log the error
    logger.error(f"Unhandled exception: {str(e)}")
    logger.error(traceback.format_exc())

    # Check if the request is expecting JSON (AJAX request)
    if (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.headers.get("Content-Type") == "application/json"
        or request.path.startswith("/api/")
    ):

        # Get the error code (default to 500)
        code = 500
        if hasattr(e, "code"):
            code = e.code

        # Return a JSON response
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), code

    # For non-JSON requests, raise the error and let Flask handle it normally
    return f"Server Error: {str(e)}", 500


class Role(db.Model):
    __tablename__ = "role"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)

    def __init__(self, name):
        self.name = name


class User(db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    # Foreign Key to the Role table
    role_id = db.Column(db.Integer, db.ForeignKey("role.id"), nullable=False)
    role = db.relationship("Role", backref="users")

    # ✅ Controls if user is active
    is_active = db.Column(db.Boolean, default=True)

    # ✅ Forces password change on first login
    must_change_password = db.Column(db.Boolean, default=False)

    def __init__(
        self, name, email, password, role_id, is_active=True, must_change_password=False
    ):
        self.name = name
        self.email = email.lower()  # Normalize email
        self.password_hash = bcrypt.hash(password)
        self.role_id = role_id
        self.is_active = is_active
        self.must_change_password = must_change_password

    def verify_password(self, password):
        return bcrypt.verify(password, self.password_hash)


class Client(db.Model):
    """Stores information about clients."""

    __tablename__ = "client"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(20))
    email = db.Column(db.String(150))
    address = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Foreign Key to the User table (salesperson/account manager)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    user = db.relationship("User", backref="clients")

    # Relationship to Jobs
    jobs = db.relationship("Job", backref="client", lazy=True)

    def __init__(self, name, user_id, phone=None, email=None, address=None):
        self.name = name
        self.user_id = user_id
        self.phone = phone
        self.email = email
        self.address = address


class Job(db.Model):
    """Represents a job which can contain multiple reports."""

    __tablename__ = "job"

    id = db.Column(db.Integer, primary_key=True)
    job_number = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Status fields (could be enums in production)
    status = db.Column(
        db.String(20), default="pending"
    )  # pending, in_progress, completed, cancelled

    # Foreign Keys
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False
    )  # Created by / Owned by
    user = db.relationship("User", backref="jobs")

    # Relationship to Reports and JobStatus
    reports = db.relationship("Report", back_populates="job", lazy=True)
    job_statuses = db.relationship("JobStatus", backref="job", lazy=True)

    def __init__(
        self, job_number, name, client_id, user_id, description=None, status="pending"
    ):
        self.job_number = job_number
        self.name = name
        self.client_id = client_id
        self.user_id = user_id
        self.description = description
        self.status = status

    @property
    def site_confirmation_status(self):
        """Get the status of the site confirmation stage."""
        status = JobStatus.query.filter_by(
            job_id=self.id, stage="site_confirmation"
        ).first()
        return status.status if status else "incomplete"

    @property
    def pre_installation_status(self):
        """Get the status of the pre-installation stage."""
        status = JobStatus.query.filter_by(
            job_id=self.id, stage="pre_installation"
        ).first()
        return status.status if status else "incomplete"

    @property
    def post_installation_status(self):
        """Get the status of the post-installation stage."""
        status = JobStatus.query.filter_by(
            job_id=self.id, stage="post_installation"
        ).first()
        return status.status if status else "incomplete"


class JobStatus(db.Model):
    """Tracks the status of different stages of a job."""

    __tablename__ = "job_status"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False)

    # Stage identifier
    stage = db.Column(
        db.String(30), nullable=False
    )  # site_confirmation, pre_installation, post_installation

    # Status fields
    status = db.Column(
        db.String(20), default="incomplete"
    )  # incomplete, in_progress, complete
    notes = db.Column(db.Text)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    completed_at = db.Column(db.DateTime)

    # User who completed this stage
    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_by = db.relationship("User", backref="completed_job_statuses")

    # Unique constraint to ensure one status per job per stage
    __table_args__ = (db.UniqueConstraint("job_id", "stage", name="_job_stage_uc"),)

    def __init__(self, job_id, stage, status="incomplete", notes=None):
        self.job_id = job_id
        self.stage = stage
        self.status = status
        self.notes = notes
        if status == "complete":
            self.completed_at = datetime.utcnow()


class Report(db.Model):
    """Stores an overall report that contains multiple measurements & an estimate."""

    __tablename__ = "report"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Direct foreign key to Job
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=True)
    job = db.relationship("Job", back_populates="reports")

    # Foreign key to the user who created/owns this report
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    user = db.relationship("User", backref="reports")

    # Relationships to Measurements and Estimates
    measurements = db.relationship("Measurement", backref="report", lazy=True)
    estimate = db.relationship("Estimate", uselist=False, backref="report")

    def __init__(self, user_id, job_id=None):
        self.user_id = user_id
        self.job_id = job_id


class Measurement(db.Model):
    """Represents an individual row in the 'Measure / Labor' table."""

    __tablename__ = "measurement"

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey("report.id"), nullable=False)

    nbr = db.Column(db.Integer, nullable=False)  # Auto-incremented row number
    style = db.Column(db.String(50), nullable=False)  # sfd, hr, sh, etc.
    config = db.Column(db.String(100))  # Optional configuration details
    width = db.Column(db.Float)  # Width (W)
    height = db.Column(db.Float)  # Height (H)
    door_design = db.Column(db.Boolean, default=False)  # Door Design (Yes/No)
    priv = db.Column(db.Boolean, default=False)  # PRIV (Yes/No)
    eg = db.Column(db.Boolean, default=False)  # EG (Yes/No)
    grids = db.Column(db.Boolean, default=False)  # Grids (Yes/No)
    grid_config = db.Column(db.String(100))  # Grid Configuration
    sr = db.Column(db.Boolean, default=False)  # S/R (Yes/No)

    def __init__(
        self,
        report_id,
        nbr,
        style,
        config,
        width,
        height,
        door_design,
        priv,
        eg,
        grids,
        grid_config,
        sr,
    ):
        self.report_id = report_id
        self.nbr = nbr
        self.style = style
        self.config = config
        self.width = width
        self.height = height
        self.door_design = door_design
        self.priv = priv
        self.eg = eg
        self.grids = grids
        self.grid_config = grid_config
        self.sr = sr


class Estimate(db.Model):
    """Stores summarized estimate data for a report."""

    __tablename__ = "estimate"

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(
        db.Integer, db.ForeignKey("report.id"), nullable=False, unique=True
    )

    extra_large_qty = db.Column(db.Integer, default=0)
    large_qty = db.Column(db.Integer, default=0)
    small_qty = db.Column(db.Integer, default=0)
    mull_qty = db.Column(db.Integer, default=0)

    sfd_qty = db.Column(db.Integer, default=0)
    dfd_qty = db.Column(db.Integer, default=0)
    sgd_qty = db.Column(db.Integer, default=0)
    extra_panels_qty = db.Column(db.Integer, default=0)
    door_design_qty = db.Column(db.Integer, default=0)
    shutter_removal_qty = db.Column(db.Integer, default=0)

    permit_cost = db.Column(db.Float, default=450.0)  # Fixed $450
    labor_total = db.Column(db.Float, default=0.0)
    marketing_fee = db.Column(db.Float, default=0.0)
    material_cost = db.Column(db.Float, default=0.0)
    salesman_cost = db.Column(db.Float, default=0.0)
    markup = db.Column(db.Float, default=5000.0)
    total_contract = db.Column(db.Float, default=0.0)
    commission = db.Column(db.Float, default=0.0)  # 20% of markup by default

    def __init__(
        self,
        report_id,
        extra_large_qty,
        large_qty,
        small_qty,
        mull_qty,
        sfd_qty,
        dfd_qty,
        sgd_qty,
        extra_panels_qty,
        door_design_qty,
        shutter_removal_qty,
        labor_total,
        marketing_fee=0,
        material_cost=0,
        markup=5000,
        salesman_cost=None,
        total_contract=None,
        commission=None,
    ):
        self.report_id = report_id
        self.extra_large_qty = extra_large_qty
        self.large_qty = large_qty
        self.small_qty = small_qty
        self.mull_qty = mull_qty
        self.sfd_qty = sfd_qty
        self.dfd_qty = dfd_qty
        self.sgd_qty = sgd_qty
        self.extra_panels_qty = extra_panels_qty
        self.door_design_qty = door_design_qty
        self.shutter_removal_qty = shutter_removal_qty
        self.labor_total = labor_total
        self.marketing_fee = marketing_fee
        self.material_cost = material_cost
        self.markup = markup

        # Calculate derived fields if not provided
        if salesman_cost is None:
            self.salesman_cost = labor_total + marketing_fee + material_cost
        else:
            self.salesman_cost = salesman_cost

        if total_contract is None:
            self.total_contract = self.salesman_cost + self.markup
        else:
            self.total_contract = total_contract

        if commission is None:
            self.commission = self.markup * 0.2
        else:
            self.commission = commission


class DashboardSetting(db.Model):
    """Stores user-specific dashboard settings and preferences."""

    __tablename__ = "dashboard_setting"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True
    )

    # Display preferences
    show_completed_jobs = db.Column(db.Boolean, default=True)
    show_pending_jobs = db.Column(db.Boolean, default=True)
    show_cancelled_jobs = db.Column(db.Boolean, default=False)
    default_time_period = db.Column(
        db.String(20), default="month"
    )  # week, month, quarter, year, all

    # Notification settings
    email_notifications = db.Column(db.Boolean, default=True)
    sms_notifications = db.Column(db.Boolean, default=False)

    user = db.relationship(
        "User", backref=db.backref("dashboard_settings", uselist=False)
    )

    def __init__(
        self,
        user_id,
        show_completed_jobs=True,
        show_pending_jobs=True,
        show_cancelled_jobs=False,
        default_time_period="month",
        email_notifications=True,
        sms_notifications=False,
    ):
        self.user_id = user_id
        self.show_completed_jobs = show_completed_jobs
        self.show_pending_jobs = show_pending_jobs
        self.show_cancelled_jobs = show_cancelled_jobs
        self.default_time_period = default_time_period
        self.email_notifications = email_notifications
        self.sms_notifications = sms_notifications


# ------------------------------------------------------------
# Example Helper Methods for Dashboard
# ------------------------------------------------------------
def get_user_dashboard_stats(user_id):
    """Get dashboard statistics for a specific user."""
    total_reports = Report.query.filter_by(user_id=user_id).count()
    total_jobs = Job.query.filter_by(user_id=user_id).count()
    completed_jobs = Job.query.filter_by(user_id=user_id, status="completed").count()
    pending_jobs = Job.query.filter_by(user_id=user_id, status="pending").count()
    in_progress_jobs = Job.query.filter_by(
        user_id=user_id, status="in_progress"
    ).count()
    total_clients = Client.query.filter_by(user_id=user_id).count()

    return {
        "total_reports": total_reports,
        "total_jobs": total_jobs,
        "completed_jobs": completed_jobs,
        "pending_jobs": pending_jobs,
        "in_progress_jobs": in_progress_jobs,
        "total_clients": total_clients,
    }


def get_dashboard_data(user_id):
    """Get all data needed for the dashboard."""
    # Get user stats
    stats = get_user_dashboard_stats(user_id)

    # Get or create user dashboard settings
    settings = DashboardSetting.query.filter_by(user_id=user_id).first()
    if not settings:
        settings = DashboardSetting(user_id=user_id)
        db.session.add(settings)
        db.session.commit()

    # Get recent jobs with their statuses
    recent_jobs = (
        Job.query.filter_by(user_id=user_id)
        .order_by(Job.updated_at.desc())
        .limit(5)
        .all()
    )

    # Gather client data
    client_ids = [job.client_id for job in recent_jobs]
    clients = {
        client.id: client
        for client in Client.query.filter(Client.id.in_(client_ids)).all()
    }

    # Format job data for display
    jobs_data = []
    for job in recent_jobs:
        client = clients.get(job.client_id)
        jobs_data.append(
            {
                "job_id": job.id,
                "job_number": job.job_number,
                "name": job.name,
                "client_id": client.id if client else None,
                "client_name": client.name if client else "Unknown",
                "client_phone": client.phone if client else None,
                "site_confirmation": job.site_confirmation_status,
                "pre_installation": job.pre_installation_status,
                "post_installation": job.post_installation_status,
                "status": job.status,
                "updated_at": job.updated_at,
            }
        )

    return {"stats": stats, "settings": settings, "recent_jobs": jobs_data}


# -----------------------------------------------------------
# Create tables + Seed Roles (if necessary)
# -----------------------------------------------------------
with app.app_context():
    db.create_all()

    # Optionally ensure we have an "admin" and "sales" role
    for default_role in ["admin", "sales"]:
        existing = Role.query.filter_by(name=default_role).first()
        if not existing:
            new_role = Role(name=default_role)
            db.session.add(new_role)
    db.session.commit()


# -----------------------------------------------------------
# Role-based Decorator
# -----------------------------------------------------------
def role_required(allowed_roles):
    """
    Decorator to restrict a route to specific user roles.
    If the user is not logged in or doesn't have an allowed role,
    redirect them back to the dashboard (or login).
    """

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user_role = session.get("role", None)  # e.g. "admin" or "sales"
            if user_role not in allowed_roles:
                flash("You are not authorized to view this page.")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)

        return wrapper

    return decorator


def get_dashboard_data_admin():
    # Stats across all users
    total_reports = Report.query.count()
    total_jobs = Job.query.count()
    completed_jobs = Job.query.filter_by(status="completed").count()
    pending_jobs = Job.query.filter_by(status="pending").count()
    in_progress_jobs = Job.query.filter_by(status="in_progress").count()
    total_clients = Client.query.count()

    stats = {
        "total_reports": total_reports,
        "total_jobs": total_jobs,
        "completed_jobs": completed_jobs,
        "pending_jobs": pending_jobs,
        "in_progress_jobs": in_progress_jobs,
        "total_clients": total_clients,
    }

    # Most recent jobs (all users)
    recent_jobs = Job.query.order_by(Job.updated_at.desc()).limit(10).all()

    # Collect client info
    client_ids = [job.client_id for job in recent_jobs]
    clients = {
        client.id: client
        for client in Client.query.filter(Client.id.in_(client_ids)).all()
    }

    jobs_data = []
    for job in recent_jobs:
        client = clients.get(job.client_id)
        jobs_data.append(
            {
                "job_id": job.id,
                "job_number": job.job_number,
                "name": job.name,
                "client_id": client.id if client else None,
                "client_name": client.name if client else "Unknown",
                "client_phone": client.phone if client else None,
                "site_confirmation": job.site_confirmation_status,
                "pre_installation": job.pre_installation_status,
                "post_installation": job.post_installation_status,
                "status": job.status,
                "updated_at": job.updated_at,
            }
        )

    return {
        "stats": stats,
        "recent_jobs": jobs_data,
        "settings": None,  # Admin doesn't need personal dashboard settings
    }


# -----------------------------------------------------------
# Authentication Routes
# -----------------------------------------------------------
@app.route("/")
def index():
    return render_template("login_signup.html")


@app.route("/login", methods=["POST"])
def login():
    email = request.form.get("login_email", "").strip().lower()
    password = request.form.get("login_password")

    # Case-insensitive email lookup
    user = User.query.filter(func.lower(User.email) == email).first()

    if user:
        if not user.is_active:
            flash("Your account is awaiting admin approval.", "error")
            return redirect(url_for("index"))

        if user.verify_password(password):
            session["user_id"] = user.id
            session["role"] = user.role.name

            # Force password change if required
            if user.must_change_password:
                flash("Please change your password before continuing.", "warning")
                return redirect(url_for("change_password"))

            flash("Login successful!", "success")
            return redirect(url_for("dashboard"))

    flash("Invalid email or password", "error")
    return redirect(url_for("index"))


@app.route("/signup", methods=["POST"])
def signup():
    name = request.form.get("signup_name")
    email = request.form.get("signup_email")
    password = request.form.get("signup_password")

    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        flash("Email already registered", "error")
        return redirect(url_for("index"))

    sales_role = Role.query.filter_by(name="sales").first()
    if not sales_role:
        sales_role = Role(name="sales")
        db.session.add(sales_role)
        db.session.commit()

    new_user = User(name=name, email=email, password=password, role_id=sales_role.id)
    db.session.add(new_user)
    db.session.commit()

    session["user_id"] = new_user.id
    session["role"] = new_user.role.name
    flash("Signup successful!", "success")
    return redirect(url_for("dashboard"))


@app.route("/change-password", methods=["GET", "POST"])
def change_password():
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    user = User.query.get(session["user_id"])

    if request.method == "POST":
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")

        if not new_password or new_password != confirm_password:
            flash("Passwords do not match or are empty.", "error")
            return redirect(url_for("change_password"))

        user.password_hash = bcrypt.hash(new_password)
        user.must_change_password = False
        db.session.commit()
        flash("Password changed successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template("change_password.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@app.route("/admin/pending-users")
@role_required(["admin"])
def view_pending_users():
    pending_users = User.query.filter_by(is_active=False).all()
    return render_template("pending_users.html", users=pending_users)


@app.route("/admin/activate-user/<int:user_id>", methods=["POST"])
@role_required(["admin"])
def activate_user(user_id):
    user = User.query.get(user_id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("view_pending_users"))

    user.is_active = True
    db.session.commit()
    flash(f"User '{user.name}' has been activated.", "success")
    return redirect(url_for("view_pending_users"))


@app.route("/admin")
@role_required(["admin"])
def admin_dashboard():
    all_users = User.query.order_by(User.id.desc()).all()
    return render_template("admin.html", users=all_users)


@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    user_id = session["user_id"]
    role = session.get("role")

    if role == "admin":
        dashboard_data = get_dashboard_data_admin()
    else:
        dashboard_data = get_dashboard_data(user_id)

    return render_template(
        "dashboard.html",
        stats=dashboard_data["stats"],
        settings=dashboard_data.get("settings"),
        recent_jobs=dashboard_data["recent_jobs"],
    )


@app.route("/update-dashboard-settings", methods=["POST"])
def update_dashboard_settings():
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    user_id = session["user_id"]

    # Get the user's dashboard settings
    settings = DashboardSetting.query.filter_by(user_id=user_id).first()
    if not settings:
        settings = DashboardSetting(user_id=user_id)
        db.session.add(settings)

    # Update settings from form data
    settings.show_completed_jobs = "show_completed_jobs" in request.form
    settings.show_pending_jobs = "show_pending_jobs" in request.form
    settings.show_cancelled_jobs = "show_cancelled_jobs" in request.form
    settings.default_time_period = request.form.get("default_time_period", "month")
    settings.email_notifications = "email_notifications" in request.form
    settings.sms_notifications = "sms_notifications" in request.form

    db.session.commit()

    flash("Dashboard settings updated successfully.", "success")
    return redirect(url_for("dashboard"))


@app.route("/api/update-job-status", methods=["POST"])
def update_job_status():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    user_id = session["user_id"]
    job_id = request.form.get("job_id")
    stage = request.form.get("stage")
    status = request.form.get("status")
    notes = request.form.get("notes")

    if not all([job_id, stage, status]):
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    # Check if job belongs to user
    job = Job.query.filter_by(id=job_id, user_id=user_id).first()
    if not job:
        return (
            jsonify({"success": False, "message": "Job not found or access denied"}),
            404,
        )

    # Get or create job status
    job_status = JobStatus.query.filter_by(job_id=job_id, stage=stage).first()
    if not job_status:
        job_status = JobStatus(job_id=job_id, stage=stage)
        db.session.add(job_status)

    # Update status
    job_status.status = status
    job_status.notes = notes

    # If status is complete, set completed_at and completed_by
    if status == "complete":
        job_status.completed_at = datetime.utcnow()
        job_status.completed_by_id = user_id

    # If all stages are complete, update the job status
    if (
        stage == "post_installation"
        and status == "complete"
        and job.site_confirmation_status == "complete"
        and job.pre_installation_status == "complete"
    ):
        job.status = "completed"

    db.session.commit()

    # Determine which column to update in the table
    column_index = {
        "site_confirmation": 5,
        "pre_installation": 6,
        "post_installation": 7,
    }.get(stage, 0)

    return jsonify(
        {
            "success": True,
            "job_id": job_id,
            "status": status,
            "stage": stage,
            "column_index": column_index,
        }
    )


@app.route("/api/delete-job/<int:job_id>", methods=["DELETE"])
def delete_job_api(job_id):
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    user_id = session["user_id"]

    # Check if the job belongs to the authenticated user
    job = Job.query.filter_by(id=job_id, user_id=user_id).first()
    if not job:
        return (
            jsonify({"success": False, "message": "Job not found or access denied"}),
            404,
        )

    try:
        # Delete associated JobStatus records
        JobStatus.query.filter_by(job_id=job_id).delete()

        # Get all associated reports and break the association by setting job_id to None
        reports = Report.query.filter_by(job_id=job_id).all()
        for report in reports:
            report.job_id = None  # Optionally, delete reports if desired

        # Delete the job itself
        db.session.delete(job)
        db.session.commit()

        return jsonify({"success": True})

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/create-job", methods=["GET", "POST"])
def create_job():
    # Ensure the user is logged in
    if "user_id" not in session:
        flash("Please log in to create a job.", "error")
        return redirect(url_for("login"))

    if request.method == "POST":
        # Retrieve form data
        job_number = request.form.get("job_number")
        name = request.form.get("name")
        client_id = request.form.get("client_id")
        description = request.form.get("description")

        # Validate required fields
        if not job_number or not name or not client_id:
            flash("Job number, name, and client are required.", "error")
            return render_template("create_job.html")

        try:
            # Create and save the new Job
            job = Job(
                job_number=job_number,
                name=name,
                client_id=int(client_id),
                user_id=session["user_id"],
                description=description,
            )
            db.session.add(job)
            db.session.commit()
            flash("Job created successfully.", "success")
            return redirect(url_for("dashboard"))
        except Exception as e:
            db.session.rollback()
            flash("Error creating job: " + str(e), "error")
            return render_template("create_job.html")
    else:
        # GET request: render the job creation form
        return render_template("create_job.html")


@app.route("/all-jobs")
def all_jobs():
    # Ensure the user is logged in
    if "user_id" not in session:
        flash("Please log in to view your jobs.", "error")
        return redirect(url_for("login"))

    user_id = session["user_id"]

    # Query all jobs for this user, ordering by most recently updated
    jobs = Job.query.filter_by(user_id=user_id).order_by(Job.updated_at.desc()).all()

    # Collect client IDs from the jobs to fetch their details
    client_ids = [job.client_id for job in jobs]
    clients = {
        client.id: client
        for client in Client.query.filter(Client.id.in_(client_ids)).all()
    }

    # Format the job data for the template
    jobs_data = []
    for job in jobs:
        client = clients.get(job.client_id)
        jobs_data.append(
            {
                "job_id": job.id,
                "job_number": job.job_number,
                "name": job.name,
                "client_id": client.id if client else None,
                "client_name": client.name if client else "Unknown",
                "client_phone": client.phone if client else None,
                "site_confirmation": job.site_confirmation_status,
                "pre_installation": job.pre_installation_status,
                "post_installation": job.post_installation_status,
                "status": job.status,
                "updated_at": job.updated_at,
            }
        )

    # Render the 'all_jobs.html' template with the list of jobs
    return render_template("all_jobs.html", jobs=jobs_data)


@app.route("/edit-job/<int:job_id>", methods=["GET", "POST"])
def edit_job(job_id):
    # Ensure the user is logged in
    if "user_id" not in session:
        flash("Please log in to edit the job.", "error")
        return redirect(url_for("login"))

    # Fetch the job owned by the logged-in user
    job = Job.query.filter_by(id=job_id, user_id=session["user_id"]).first()
    if not job:
        flash("Job not found or access denied.", "error")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        # Update job details from the form
        job.job_number = request.form.get("job_number")
        job.name = request.form.get("name")
        job.description = request.form.get("description")
        # Assuming client_id is editable via the form
        client_id = request.form.get("client_id")
        if client_id:
            job.client_id = int(client_id)
        try:
            db.session.commit()
            flash("Job updated successfully.", "success")
            return redirect(url_for("dashboard"))
        except Exception as e:
            db.session.rollback()
            flash("Error updating job: " + str(e), "error")
    # Render the edit job form template
    return render_template("edit_job.html", job=job)


@app.route("/view-job/<int:job_id>")
def view_job(job_id):
    # Ensure the user is logged in
    if "user_id" not in session:
        flash("Please log in to view the job.", "error")
        return redirect(url_for("login"))

    # Fetch the job owned by the logged-in user
    job = Job.query.filter_by(id=job_id, user_id=session["user_id"]).first()
    if not job:
        flash("Job not found or access denied.", "error")
        return redirect(url_for("dashboard"))

    # Optionally, fetch any reports or related data for this job
    reports = Report.query.filter_by(job_id=job_id).all()

    # Render the view job details template
    return render_template("view_job.html", job=job, reports=reports)


@app.route("/create-report/<int:job_id>", methods=["GET", "POST"])
def create_report(job_id):
    # Ensure the user is logged in
    if "user_id" not in session:
        flash("Please log in to create a report.", "error")
        return redirect(url_for("login"))

    # Verify that the job exists and belongs to the user
    job = Job.query.filter_by(id=job_id, user_id=session["user_id"]).first()
    if not job:
        flash("Job not found or access denied.", "error")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        try:
            # Create a new report linked to this job and user
            report = Report(user_id=session["user_id"], job_id=job_id)
            # You can process additional form fields for report details here
            db.session.add(report)
            db.session.commit()
            flash("Report created successfully.", "success")
            return redirect(url_for("view_job", job_id=job_id))
        except Exception as e:
            db.session.rollback()
            flash("Error creating report: " + str(e), "error")
    # Render the report creation form template
    return render_template("create_report.html", job=job)


@app.route("/job-timeline/<int:job_id>")
def job_timeline(job_id):
    # Ensure the user is logged in
    if "user_id" not in session:
        flash("Please log in to view the job timeline.", "error")
        return redirect(url_for("login"))

    # Verify that the job exists and belongs to the user
    job = Job.query.filter_by(id=job_id, user_id=session["user_id"]).first()
    if not job:
        flash("Job not found or access denied.", "error")
        return redirect(url_for("dashboard"))

    # Retrieve the job's timeline data (e.g., all related JobStatus entries)
    timeline = (
        JobStatus.query.filter_by(job_id=job_id).order_by(JobStatus.created_at).all()
    )

    # Render the job timeline template
    return render_template("job_timeline.html", job=job, timeline=timeline)


@app.route("/profile")
def profile():
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))
    return render_template("profile.html")


@app.route("/site_confirmation")
def site_confirmation():
    return render_template("site_confirmation.html")


@app.route("/pre_installation")
def pre_installation():
    return render_template("pre_installation.html")


@app.route("/post_installation")
def post_installation():
    return render_template("post_installation.html")


@app.route("/measure_labor")
def measure_labor():
    return render_template("measure_labor.html")


# Add this route to your Flask application
@app.route("/check_login", methods=["GET"])
def check_login():
    """Endpoint to check if a user is logged in"""
    if "user_id" not in session:
        return jsonify({"logged_in": False}), 200

    # If the user is logged in, get their info
    user = User.query.get(session["user_id"])
    if not user:
        # User ID in session but not in database
        session.clear()
        return jsonify({"logged_in": False}), 200

    return (
        jsonify(
            {
                "logged_in": True,
                "user_id": user.id,
                "user_email": user.email,
                "user_role": session.get("role", "unknown"),
            }
        ),
        200,
    )


# ========================= New Pages =========================
# 1) Settings
@app.route("/settings")
# @role_required(["admin", "sales"])  # Both roles can see this
def settings():
    return render_template("settings.html")


# 2) Reports (Only Admin)
@app.route("/reports")
# @role_required(["admin"])
def reports():
    return render_template("reports.html")


# 3) Clients (Admin & Sales)
@app.route("/clients")
# @role_required(["admin", "sales"])
def clients():
    return render_template("clients.html")


# -----------------------------------------------------------
# Routes for saving reports
# -----------------------------------------------------------
@app.route("/save_report", methods=["POST"])
def save_report():
    """Save a new report with measurements and estimate"""
    import traceback
    import logging

    # Set up logging
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)

    # Check if this is an AJAX request
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    logger.info(f"Request is AJAX: {is_ajax}")

    # Check if user is logged in
    if "user_id" not in session:
        logger.error("User is not logged in")
        if is_ajax:
            return (
                jsonify({"error": "Your session has expired. Please log in again."}),
                401,
            )
        else:
            flash("Your session has expired. Please log in again.", "error")
            return redirect(url_for("index"))

    # Log session data for debugging
    logger.info(f"Session data: {dict(session)}")

    try:
        # Get JSON data from the request
        try:
            data = request.get_json()
            if not data:
                logger.error("No JSON data in request")
                return jsonify({"error": "No data provided"}), 400

            logger.info(f"Received data: {data.keys()}")
        except Exception as e:
            logger.error(f"Error parsing JSON: {str(e)}")
            return jsonify({"error": f"Invalid JSON: {str(e)}"}), 400

        # Get user ID from session
        user_id = session.get("user_id")
        logger.info(f"Processing for user_id: {user_id}")

        # Check if user exists in database
        user = User.query.get(user_id)
        if not user:
            logger.error(f"User ID {user_id} not found in database")
            return jsonify({"error": "User not found. Please log in again."}), 401

        # Create a new report within a transaction
        try:
            # Start transaction by creating the report
            report = Report(user_id=user_id)
            db.session.add(report)
            db.session.flush()  # Get the ID without committing yet
            logger.info(f"Created report with ID: {report.id}")

            # Validate and process measurements
            if "measurements" not in data or not data["measurements"]:
                logger.error("No measurements data provided")
                return jsonify({"error": "No measurement data provided"}), 400

            measurement_data = data["measurements"]
            logger.info(f"Processing {len(measurement_data)} measurements")

            # Process each measurement
            for i, item in enumerate(measurement_data):
                logger.debug(f"Processing measurement {i+1}: {item}")

                # Make sure required fields are present
                if not item.get("style"):
                    logger.error(f"Missing style in measurement {i+1}")
                    db.session.rollback()
                    return (
                        jsonify({"error": f"Missing style in measurement {i+1}"}),
                        400,
                    )

                # Convert numeric fields
                try:
                    width = float(item.get("width")) if item.get("width") else None
                    height = float(item.get("height")) if item.get("height") else None
                except ValueError as ve:
                    logger.error(f"Invalid numeric value: {str(ve)}")
                    db.session.rollback()
                    return (
                        jsonify(
                            {"error": f"Invalid numeric value in row {i+1}: {str(ve)}"}
                        ),
                        400,
                    )

                # Convert boolean fields
                door_design = item.get("door_design") == "Yes"
                priv = item.get("priv") == "Yes"
                eg = item.get("eg") == "Yes"
                grids = item.get("grids") == "Yes"
                sr = item.get("sr") == "Yes"

                # Create and add the measurement
                measurement = Measurement(
                    report_id=report.id,
                    nbr=(
                        int(item.get("nbr"))
                        if item.get("nbr") and str(item.get("nbr")).isdigit()
                        else i + 1
                    ),
                    style=item.get("style"),
                    config=item.get("config", ""),
                    width=width,
                    height=height,
                    door_design=door_design,
                    priv=priv,
                    eg=eg,
                    grids=grids,
                    grid_config=item.get("grid_config", ""),
                    sr=sr,
                )
                db.session.add(measurement)

            logger.info("All measurements processed successfully")

            # Process estimate data
            if "estimate" not in data:
                logger.error("No estimate data provided")
                db.session.rollback()
                return jsonify({"error": "No estimate data provided"}), 400

            estimate_data = data["estimate"]
            logger.info(f"Processing estimate data: {estimate_data}")

            # Create the estimate with numeric conversions
            try:
                # Helper function to safely convert values
                def safe_convert(value, default=0):
                    if value is None:
                        return default
                    try:
                        return float(value)
                    except (ValueError, TypeError):
                        return default

                # Create the estimate object
                estimate = Estimate(
                    report_id=report.id,
                    extra_large_qty=int(
                        safe_convert(estimate_data.get("extra_large_qty"))
                    ),
                    large_qty=int(safe_convert(estimate_data.get("large_qty"))),
                    small_qty=int(safe_convert(estimate_data.get("small_qty"))),
                    mull_qty=int(safe_convert(estimate_data.get("mull_qty"))),
                    sfd_qty=int(safe_convert(estimate_data.get("sfd_qty"))),
                    dfd_qty=int(safe_convert(estimate_data.get("dfd_qty"))),
                    sgd_qty=int(safe_convert(estimate_data.get("sgd_qty"))),
                    extra_panels_qty=int(
                        safe_convert(estimate_data.get("extra_panels_qty"))
                    ),
                    door_design_qty=int(
                        safe_convert(estimate_data.get("door_design_qty"))
                    ),
                    shutter_removal_qty=int(
                        safe_convert(estimate_data.get("shutter_removal_qty"))
                    ),
                    labor_total=safe_convert(estimate_data.get("labor_total")),
                    marketing_fee=safe_convert(estimate_data.get("marketing_fee")),
                    material_cost=safe_convert(estimate_data.get("material_cost")),
                    markup=safe_convert(estimate_data.get("markup"), 5000),
                    salesman_cost=safe_convert(estimate_data.get("salesman_cost")),
                    total_contract=safe_convert(estimate_data.get("total_contract")),
                    commission=safe_convert(estimate_data.get("commission")),
                )
                db.session.add(estimate)
                logger.info("Estimate created successfully")

            except Exception as e:
                logger.error(f"Error creating estimate: {str(e)}")
                logger.error(traceback.format_exc())
                db.session.rollback()
                return jsonify({"error": f"Error creating estimate: {str(e)}"}), 500

            # If we've made it here, commit the transaction
            db.session.commit()
            logger.info(f"Report {report.id} saved successfully")

            return jsonify(
                {
                    "success": True,
                    "message": "Report saved successfully!",
                    "report_id": report.id,
                }
            )

        except Exception as e:
            logger.error(f"Error processing report data: {str(e)}")
            logger.error(traceback.format_exc())
            db.session.rollback()
            return jsonify({"error": f"Error processing report: {str(e)}"}), 500

    except Exception as e:
        logger.error(f"Unhandled exception: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"Server error: {str(e)}"}), 500


# Test report saving Flask app
@app.route("/test_save", methods=["POST"])
def test_save():
    """A minimal test endpoint to verify basic save functionality"""
    import logging

    logger = logging.getLogger(__name__)
    logger.info("Test save endpoint called")

    # Log request info
    logger.info(f"Session data: {dict(session)}")

    # Check if user is logged in
    if "user_id" not in session:
        logger.error("User not logged in")
        return jsonify({"error": "Not logged in"}), 401

    # Try to get the request data
    try:
        data = request.get_json()
        logger.info(f"Received data: {data}")
    except Exception as e:
        logger.error(f"Error parsing JSON: {str(e)}")
        return jsonify({"error": f"JSON parse error: {str(e)}"}), 400

    # Try a simple database operation
    try:
        # Create a test report
        report = Report(user_id=session["user_id"])
        db.session.add(report)
        db.session.commit()

        logger.info(f"Successfully created test report ID: {report.id}")
        return jsonify(
            {
                "success": True,
                "message": "Test report created successfully",
                "report_id": report.id,
            }
        )
    except Exception as e:
        logger.error(f"Database error: {str(e)}")
        db.session.rollback()
        return jsonify({"error": f"Database error: {str(e)}"}), 500


# -----------------------------------------------------------
# GHL Webhook
# -----------------------------------------------------------
from sqlalchemy import func  # add this at the top if not already imported


@app.route("/webhook/opportunity", methods=["POST"])
def receive_ghl_opportunity():
    try:
        data = request.get_json()
        logger.info(f"Received GHL opportunity webhook data: {data}")

        # Extract contact info
        name = (
            data.get("full_name")
            or f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
        )
        phone = data.get("phone")
        email = data.get("email")
        job_name = data.get("opportunity_name", "New Job")
        description = f"Pipeline: {data.get('pipeline_name', 'Unknown')}, Status: {data.get('status', 'N/A')}"

        # Assigned user email from GHL
        assigned_user_email = data.get("user", {}).get("email")
        if assigned_user_email:
            assigned_user_email = assigned_user_email.lower()

        if not name or not assigned_user_email:
            logger.warning("Missing name or assigned_user_email")
            return jsonify({"error": "Missing name or assigned_user_email"}), 400

        # Case-insensitive email lookup
        user = User.query.filter(func.lower(User.email) == assigned_user_email).first()

        if not user:
            logger.warning(f"Assigned user not found: {assigned_user_email}")
            sales_role = Role.query.filter_by(name="sales").first()

            if not sales_role:
                logger.error("Sales role not found. Cannot assign role to new user.")
                return jsonify({"error": "Sales role not defined in system"}), 500

            # Construct fallback name
            first_name = data.get("user", {}).get("firstName", "")
            last_name = data.get("user", {}).get("lastName", "")
            generated_name = f"{first_name} {last_name}".strip() or assigned_user_email

            # Auto-create an inactive user
            user = User(
                name=generated_name,
                email=assigned_user_email,
                password="Temp@1234",  # Placeholder (can later enforce reset)
                role_id=sales_role.id,
                is_active=True,
                must_change_password=True,
            )
            user.is_active = False  # New field you must add to your User model
            db.session.add(user)
            db.session.flush()
            logger.info(f"Auto-created inactive user: {user.email}")

        # Create client
        client = Client(name=name, phone=phone, email=email, user_id=user.id)
        db.session.add(client)
        db.session.flush()

        # Generate next job number
        latest_job = Job.query.order_by(Job.id.desc()).first()
        next_job_number = f"JOB-{latest_job.id + 1:05d}" if latest_job else "JOB-00001"

        # Create job
        job = Job(
            job_number=next_job_number,
            name=job_name,
            client_id=client.id,
            user_id=user.id,
            description=description,
        )
        db.session.add(job)
        db.session.flush()

        # Create 3 job status stages
        for stage in ["site_confirmation", "pre_installation", "post_installation"]:
            job_status = JobStatus(job_id=job.id, stage=stage, status="incomplete")
            db.session.add(job_status)

        db.session.commit()
        logger.info(
            f"Created client '{client.name}', job '{job.job_number}' assigned to '{user.email}'"
        )

        return (
            jsonify({"message": "Client, job, and statuses created successfully"}),
            200,
        )

    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        logger.error(traceback.format_exc())
        db.session.rollback()
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


# -----------------------------------------------------------
# Run the Application
# -----------------------------------------------------------
if __name__ == "__main__":
    # Create tables if they don't exist already
    db.create_all()
    app.run(debug=True)
