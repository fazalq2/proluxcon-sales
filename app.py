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
from werkzeug.utils import secure_filename

# Flask imports
from flask import make_response, send_file

# PDF generation
import pdfkit
import tempfile

# Excel generation
import xlsxwriter


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

app.config["UPLOAD_FOLDER"] = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "uploads"
)
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


@app.before_request
def make_session_permanent():
    session.permanent = True


# Template context processor for PDF timestamps
@app.context_processor
def utility_processor():
    def now():
        return datetime.utcnow()

    return dict(now=now)


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

    # NEW FIELDS FOR STORING OPPORTUNITY DATA
    opportunity_data = db.Column(
        db.JSON, nullable=True
    )  # Store all opportunity data as JSON
    address = db.Column(db.String(255), nullable=True)  # From 'Job Address:'
    service_types = db.Column(db.JSON, nullable=True)  # From 'What Services?'
    timeframe = db.Column(
        db.String(50), nullable=True
    )  # From 'When Do You Need Service?'
    property_type = db.Column(db.String(100), nullable=True)  # From 'Property Type'
    hoa_status = db.Column(
        db.String(10), nullable=True
    )  # From 'Do you live in and HOA?'
    hoa_name = db.Column(db.String(100), nullable=True)  # From 'HOA Name?'
    opportunity_source = db.Column(db.String(100), nullable=True)  # From 'source'
    contact_type = db.Column(db.String(50), nullable=True)  # From 'contact_type'
    pipeline_stage = db.Column(db.String(100), nullable=True)  # From 'pipleline_stage'

    # Additional detailed fields
    num_windows = db.Column(db.Integer, nullable=True)  # From 'How many windows'
    num_doors = db.Column(db.Integer, nullable=True)  # From 'How many doors'
    roof_type = db.Column(db.JSON, nullable=True)  # From 'Roof type'
    message = db.Column(db.Text, nullable=True)  # From 'Message'

    # Relationships
    user = db.relationship("User", backref="jobs")
    reports = db.relationship("Report", back_populates="job", lazy=True)
    job_statuses = db.relationship("JobStatus", backref="job", lazy=True)
    documents = db.relationship("JobDocument", back_populates="job", lazy=True)

    def __init__(
        self,
        job_number,
        name,
        client_id,
        user_id,
        description=None,
        status="pending",
        opportunity_data=None,
        address=None,
        service_types=None,
        timeframe=None,
        property_type=None,
        hoa_status=None,
        hoa_name=None,
        opportunity_source=None,
        contact_type=None,
        pipeline_stage=None,
        num_windows=None,
        num_doors=None,
        roof_type=None,
        message=None,
    ):
        self.job_number = job_number
        self.name = name
        self.client_id = client_id
        self.user_id = user_id
        self.description = description
        self.status = status

        # Initialize new opportunity fields
        self.opportunity_data = opportunity_data or {}
        self.address = address
        self.service_types = service_types or []
        self.timeframe = timeframe
        self.property_type = property_type
        self.hoa_status = hoa_status
        self.hoa_name = hoa_name
        self.opportunity_source = opportunity_source
        self.contact_type = contact_type
        self.pipeline_stage = pipeline_stage

        # Initialize additional detailed fields
        self.num_windows = num_windows
        self.num_doors = num_doors
        self.roof_type = roof_type or []
        self.message = message

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

    @property
    def services_summary(self):
        """Returns a human-readable summary of services requested."""
        if not self.service_types:
            return "No services specified"

        return ", ".join(self.service_types)

    @property
    def has_hoa(self):
        """Returns True if property has HOA, False otherwise."""
        return self.hoa_status == "YES"


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


class JobDocument(db.Model):
    __tablename__ = "job_document"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # Title or short name of the doc, e.g. "Signed Checklist", "HOA Approval Form"
    title = db.Column(db.String(200), nullable=False)

    # e.g. "application/pdf", "image/jpeg"
    mime_type = db.Column(db.String(50), nullable=True)

    # The filename as stored in your filesystem or DB
    filename = db.Column(db.String(255), nullable=False)

    # Optional: if you store the file in S3 or a local path
    file_path = db.Column(db.String(500), nullable=True)

    # Alternatively, if you store the raw binary in the DB:
    # file_data = db.Column(db.LargeBinary)

    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationship
    job = db.relationship("Job", back_populates="documents")
    user = db.relationship("User", backref="uploaded_documents")

    def __init__(
        self, job_id, user_id, title, filename, mime_type=None, file_path=None
    ):
        self.job_id = job_id
        self.user_id = user_id
        self.title = title
        self.filename = filename
        self.mime_type = mime_type
        self.file_path = file_path


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


class SiteConfirmation(db.Model):
    """Stores information related to site confirmation for a job."""

    __tablename__ = "site_confirmation"

    id = db.Column(db.Integer, primary_key=True)

    # One-to-One with Job
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False, unique=True)
    job = db.relationship("Job", backref=db.backref("site_confirmation", uselist=False))

    # Status tracking
    is_completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime)
    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_by = db.relationship("User", backref="completed_site_confirmations")

    # Document section tracking
    has_floor_plan = db.Column(db.Boolean, default=False)
    has_material_quote = db.Column(db.Boolean, default=False)
    has_signed_agreement = db.Column(db.Boolean, default=False)
    has_sales_checklist = db.Column(db.Boolean, default=False)

    # For combined document uploads
    has_combined_document = db.Column(db.Boolean, default=False)
    combined_document_notes = db.Column(db.Text)  # To store page number notes

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Additional notes
    notes = db.Column(db.Text)

    def __init__(self, job_id, notes=None):
        self.job_id = job_id
        self.notes = notes

    def mark_completed(self, user_id):
        """Mark the site confirmation as completed by a specific user."""
        self.is_completed = True
        self.completed_at = datetime.utcnow()
        self.completed_by_id = user_id

        # Also update the corresponding JobStatus entry
        job_status = JobStatus.query.filter_by(
            job_id=self.job_id, stage="site_confirmation"
        ).first()
        if job_status:
            job_status.status = "complete"
            job_status.completed_at = datetime.utcnow()
            job_status.completed_by_id = user_id
            job_status.notes = "Completed through site confirmation form"
        else:
            # Create a new job status if one doesn't exist
            new_status = JobStatus(
                job_id=self.job_id,
                stage="site_confirmation",
                status="complete",
                notes="Completed through site confirmation form",
            )
            db.session.add(new_status)


class SiteConfirmationDocument(db.Model):
    """Tracks documents specifically for site confirmation purposes."""

    __tablename__ = "site_confirmation_document"

    id = db.Column(db.Integer, primary_key=True)

    # Link to the site confirmation
    site_confirmation_id = db.Column(
        db.Integer, db.ForeignKey("site_confirmation.id"), nullable=False
    )
    site_confirmation = db.relationship("SiteConfirmation", backref="documents")

    # Link to the job document (which stores the actual file)
    job_document_id = db.Column(
        db.Integer, db.ForeignKey("job_document.id"), nullable=False
    )
    job_document = db.relationship("JobDocument")

    # Document type/category
    document_type = db.Column(
        db.String(50), nullable=False
    )  # 'floor_plan', 'material_quote', 'signed_agreement', 'sales_checklist', 'combined', 'additional'

    # For combined documents, track page numbers
    page_range = db.Column(db.String(50))  # e.g., "1-3" for pages 1 through 3

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __init__(
        self, site_confirmation_id, job_document_id, document_type, page_range=None
    ):
        self.site_confirmation_id = site_confirmation_id
        self.job_document_id = job_document_id
        self.document_type = document_type
        self.page_range = page_range


class PreInstallation(db.Model):
    __tablename__ = "pre_installation"

    id = db.Column(db.Integer, primary_key=True)

    # one‑to‑one with Job
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False, unique=True)
    job = db.relationship("Job", backref=db.backref("pre_installation", uselist=False))

    # basic workflow tracking
    is_completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime)
    completed_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    notes = db.Column(db.Text)

    # document flags  (add / remove as required)
    has_custom_door_design = db.Column(db.Boolean, default=False)
    has_mod_dap = db.Column(db.Boolean, default=False)
    has_approved_proposal = db.Column(db.Boolean, default=False)
    has_final_material_order = db.Column(db.Boolean, default=False)
    has_final_permit_floor_plan = db.Column(db.Boolean, default=False)
    has_job_site_photos = db.Column(db.Boolean, default=False)

    # radio‑/checkbox style fields
    door_option = db.Column(db.String(30))  # option1 / option2
    casing_standard = db.Column(db.Boolean, default=False)
    casing_special = db.Column(db.Boolean, default=False)

    cleaning_dust = db.Column(db.Boolean, default=False)
    cleaning_vacuum = db.Column(db.Boolean, default=False)
    cleaning_mop = db.Column(db.Boolean, default=False)
    cleaning_change_beds = db.Column(db.Boolean, default=False)
    cleaning_windows = db.Column(db.Boolean, default=False)
    cleaning_none = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def mark_completed(self, user_id: int):
        self.is_completed = True
        self.completed_at = datetime.utcnow()
        self.completed_by = user_id

        # update JobStatus record
        status = JobStatus.query.filter_by(
            job_id=self.job_id, stage="pre_installation"
        ).first()
        if not status:
            status = JobStatus(job_id=self.job_id, stage="pre_installation")
            db.session.add(status)

        status.status = "complete"
        status.completed_at = self.completed_at
        status.completed_by_id = user_id


class PreInstallationDocument(db.Model):
    """
    Documents uploaded from the pre‑installation sheet.
    """

    __tablename__ = "pre_installation_document"

    id = db.Column(db.Integer, primary_key=True)
    pre_installation_id = db.Column(
        db.Integer, db.ForeignKey("pre_installation.id"), nullable=False
    )
    pre_installation = db.relationship("PreInstallation", backref="documents")

    job_document_id = db.Column(
        db.Integer, db.ForeignKey("job_document.id"), nullable=False
    )
    job_document = db.relationship("JobDocument")

    document_type = db.Column(db.String(50), nullable=False)  # custom_door_design …
    page_range = db.Column(db.String(50))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)


class PostInstallation(db.Model):
    __tablename__ = "post_installation"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False, unique=True)
    job = db.relationship("Job", backref=db.backref("post_installation", uselist=False))

    # Status tracking
    is_completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime)
    completed_by = db.Column(db.Integer, db.ForeignKey("user.id"))

    # Notes
    notes = db.Column(db.Text)

    # Additional fields as JSON
    finish_items = db.Column(db.JSON)
    labor_items = db.Column(db.JSON)
    parts_items = db.Column(db.JSON)
    permit_items = db.Column(db.JSON)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __init__(self, job_id, notes=None):
        self.job_id = job_id
        self.notes = notes
        self.finish_items = []
        self.labor_items = []
        self.parts_items = []
        self.permit_items = []

    def mark_completed(self, user_id: int):
        self.is_completed = True
        self.completed_at = datetime.utcnow()
        self.completed_by = user_id

        # update JobStatus record
        status = JobStatus.query.filter_by(
            job_id=self.job_id, stage="post_installation"
        ).first()
        if not status:
            status = JobStatus(job_id=self.job_id, stage="post_installation")
            db.session.add(status)

        status.status = "complete"
        status.completed_at = self.completed_at
        status.completed_by_id = user_id


class PostInstallationDocument(db.Model):
    """Documents uploaded from the post-installation sheet."""

    __tablename__ = "post_installation_document"

    id = db.Column(db.Integer, primary_key=True)
    post_installation_id = db.Column(
        db.Integer, db.ForeignKey("post_installation.id"), nullable=False
    )
    post_installation = db.relationship("PostInstallation", backref="documents")

    job_document_id = db.Column(
        db.Integer, db.ForeignKey("job_document.id"), nullable=False
    )
    job_document = db.relationship("JobDocument")

    document_type = db.Column(
        db.String(50), nullable=False
    )  # permit_plan, final_invoice, etc.
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)


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


class WindowLeadChecklist(db.Model):
    __tablename__ = "window_lead_checklist"

    id = db.Column(db.Integer, primary_key=True)

    # One-to-One with Job
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False, unique=True)
    job = db.relationship(
        "Job", backref=db.backref("window_lead_checklist", uselist=False)
    )

    # Example: top checkboxes
    material_order = db.Column(db.Boolean, default=False)
    property_appraiser_snapshot = db.Column(db.Boolean, default=False)
    measure_labor_sheet = db.Column(db.Boolean, default=False)
    property_appraiser_building_sketch = db.Column(db.Boolean, default=False)
    property_photos_labeled = db.Column(db.Boolean, default=False)

    # Example: Basic fields
    date = db.Column(db.Date)  # or db.DateTime if you prefer
    sales_rep = db.Column(db.String(150))
    client_name = db.Column(db.String(150))
    job_address = db.Column(db.String(255))

    # HOA info
    hoa_yes = db.Column(db.Boolean, default=False)
    hoa_no = db.Column(db.Boolean, default=False)
    hoa_community_name = db.Column(db.String(255))

    # Payment type
    is_cash = db.Column(db.Boolean, default=False)
    is_finance = db.Column(db.Boolean, default=False)
    finance_type = db.Column(db.String(50))  # e.g. "Ygrene", "GoodLeap", etc.

    # Financing terms
    term_0_interest = db.Column(db.Boolean, default=False)
    term_5_year = db.Column(db.Boolean, default=False)
    term_10_year = db.Column(db.Boolean, default=False)
    term_15_year = db.Column(db.Boolean, default=False)
    term_20_year = db.Column(db.Boolean, default=False)

    monthly_budget = db.Column(db.String(50))

    # Purpose
    purpose_insurance = db.Column(db.Boolean, default=False)
    purpose_rental = db.Column(db.Boolean, default=False)
    purpose_remodel = db.Column(db.Boolean, default=False)
    purpose_house_flip = db.Column(db.Boolean, default=False)
    purpose_new_construction = db.Column(db.Boolean, default=False)

    # Project Horizon
    horizon_asap = db.Column(db.Boolean, default=False)
    horizon_30_days = db.Column(db.Boolean, default=False)
    horizon_2_3_months = db.Column(db.Boolean, default=False)
    horizon_woft = db.Column(db.Boolean, default=False)  # "W.O.F.T"

    # Extra notes
    notes = db.Column(db.Text)

    # Example enclosure & structural fields
    encl_photo_with_sketch = db.Column(db.Boolean, default=False)
    encl_notated_areas = db.Column(db.Boolean, default=False)
    encl_existing_sliding_door_remain = db.Column(db.Boolean, default=False)
    building_3_stories = db.Column(db.Boolean, default=False)
    link_afceng = db.Column(
        db.String(255)
    )  # e.g. URL https://www.afcengcart.com/propertyinfo.aspx
    structural_modifications = db.Column(db.Boolean, default=False)
    structural_photo_area_drawn = db.Column(db.Boolean, default=False)
    structural_photo_in_out = db.Column(db.Boolean, default=False)


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


def get_job_stats(job_id):
    """Get comprehensive stats for a job"""
    stats = {}

    # Get job
    job = Job.query.get(job_id)
    if not job:
        return None

    # Basic job info
    stats["job"] = job

    # Count reports
    stats["report_count"] = Report.query.filter_by(job_id=job_id).count()

    # Get total contract value
    total_value = 0
    for estimate in Estimate.query.join(Report).filter(Report.job_id == job_id).all():
        total_value += estimate.total_contract

    stats["total_value"] = total_value

    # Status counts
    status_counts = {
        "site_confirmation": {"incomplete": 0, "in_progress": 0, "complete": 0},
        "pre_installation": {"incomplete": 0, "in_progress": 0, "complete": 0},
        "post_installation": {"incomplete": 0, "in_progress": 0, "complete": 0},
    }

    for status in JobStatus.query.filter_by(job_id=job_id).all():
        if (
            status.stage in status_counts
            and status.status in status_counts[status.stage]
        ):
            status_counts[status.stage][status.status] += 1

    stats["status_counts"] = status_counts

    return stats


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


@app.route("/update_job_from_reports/<int:job_id>", methods=["POST"])
def update_job_from_reports(job_id):
    """Update job status based on reports"""
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    # Verify job exists and user has permission
    job = Job.query.get_or_404(job_id)
    if job.user_id != session["user_id"] and session.get("role") != "admin":
        flash("Job not found or access denied.", "error")
        return redirect(url_for("dashboard"))

    # Count reports
    report_count = Report.query.filter_by(job_id=job_id).count()

    # Logic: If there's at least one report, move the job to "in_progress"
    if report_count > 0 and job.status == "pending":
        job.status = "in_progress"
        db.session.commit()
        flash("Job status updated to In Progress based on attached reports.", "success")

    return redirect(url_for("view_job", job_id=job_id))


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


# Create Jobs
@app.route("/create-job", methods=["GET", "POST"])
def create_job():
    if "user_id" not in session:
        flash("Please log in to create a job.", "error")
        return redirect(url_for("login"))

    user_id = session["user_id"]
    role = session.get("role", "sales")

    if request.method == "POST":
        # Retrieve form data
        job_number = request.form.get("job_number", "").strip()
        name = request.form.get("name", "").strip()
        status = request.form.get("status", "pending").strip()
        client_id = request.form.get("client_id")
        description = request.form.get("description", "").strip()
        installation_address = request.form.get("installation_address", "").strip()

        # Optional: generate a job_number if empty
        if not job_number:
            job_number = _generate_next_job_number()

        if not name or not client_id:
            flash("Job name and client are required fields.", "error")
            return render_template(
                "create_job.html",
                clients=_get_clients_for_user(),
                sales_users=_get_sales_users_if_admin(),
                entered_data=request.form,
            )

        # If admin, use assigned_user_id if provided, else default to the admin themselves
        if role == "admin":
            assigned_user_id = request.form.get("assigned_user_id")
            if assigned_user_id:
                # Make sure assigned_user_id is valid
                assigned_user = User.query.filter_by(id=assigned_user_id).first()
                if not assigned_user or assigned_user.role.name != "sales":
                    flash("Invalid assigned user.", "error")
                    return render_template(
                        "create_job.html",
                        clients=_get_clients_for_user(),
                        sales_users=_get_sales_users_if_admin(),
                        entered_data=request.form,
                    )
                final_assigned_user_id = assigned_user_id
            else:
                final_assigned_user_id = user_id
        else:
            final_assigned_user_id = user_id

        # Verify the selected client is valid for the current user’s role
        client = _get_client_if_allowed(client_id)
        if not client:
            flash(
                "Invalid client or you don't have permission for this client.", "error"
            )
            return render_template(
                "create_job.html",
                clients=_get_clients_for_user(),
                sales_users=_get_sales_users_if_admin(),
                entered_data=request.form,
            )

        # Create new Job
        try:
            new_job = Job(
                job_number=job_number,
                name=name,
                client_id=client.id,
                user_id=final_assigned_user_id,  # <--- The assigned user
                status=status,
                description=description,
            )
            # If you have an installation_address field on Job:
            new_job.installation_address = installation_address

            db.session.add(new_job)
            db.session.commit()
            flash("Job created successfully!", "success")
            return redirect(url_for("dashboard"))

        except Exception as e:
            db.session.rollback()
            flash(f"Error creating job: {str(e)}", "error")
            return render_template(
                "create_job.html",
                clients=_get_clients_for_user(),
                sales_users=_get_sales_users_if_admin(),
                entered_data=request.form,
            )

    else:
        # GET request
        return render_template(
            "create_job.html",
            clients=_get_clients_for_user(),
            sales_users=_get_sales_users_if_admin(),
        )


def _get_sales_users_if_admin():
    """
    Returns a list of sales users if current user is admin,
    otherwise returns None or empty list.
    """
    role = session.get("role")
    if role == "admin":
        # Fetch all active users with the 'sales' role
        sales_role = Role.query.filter_by(name="sales").first()
        if not sales_role:
            return []
        return (
            User.query.filter_by(role_id=sales_role.id, is_active=True)
            .order_by(User.name.asc())
            .all()
        )
    else:
        return []


def _get_clients_for_user():
    """
    Helper function to fetch relevant clients for the current user.
    Admin sees all; sales sees only their own clients.
    """
    if "role" not in session or "user_id" not in session:
        return []
    user_role = session["role"]
    user_id = session["user_id"]

    if user_role == "admin":
        return Client.query.order_by(Client.name.asc()).all()
    else:
        return Client.query.filter_by(user_id=user_id).order_by(Client.name.asc()).all()


def _get_client_if_allowed(client_id):
    """
    Returns the client if the user (sales) is authorized to use it,
    or if the user is admin. Otherwise returns None.
    """
    if not client_id:
        return None

    user_role = session.get("role")
    user_id = session.get("user_id")

    client = Client.query.get(client_id)
    if not client:
        return None

    if user_role == "admin":
        return client

    # For sales, client must belong to the same user
    if client.user_id == user_id:
        return client
    return None


def _generate_next_job_number():
    """
    Example logic to generate a new job_number like 'JOB-00001'
    based on the highest existing ID or some other logic.
    """
    latest_job = Job.query.order_by(Job.id.desc()).first()
    if latest_job:
        # Extract the numeric part from the last job number, or just use ID
        new_id = latest_job.id + 1
        return f"JOB-{new_id:05d}"
    else:
        return "JOB-00001"


@app.route("/all-jobs", methods=["GET"])
def all_jobs():
    """
    Displays a list of jobs:
      - Admin sees all jobs.
      - Sales sees only their jobs.
      - Supports optional search and status filtering.
    """
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    user_id = session["user_id"]
    user_role = session.get("role", "sales")  # default to 'sales' if not found

    # Get query parameters for search and status filtering
    search_query = request.args.get("q", "").strip().lower()
    status_filter = request.args.get("status", "").strip().lower()  # e.g. "in_progress"

    # 1) Base query
    if user_role == "admin":
        query = Job.query
    else:
        # Restrict to current user's jobs if not admin
        query = Job.query.filter_by(user_id=user_id)

    # 2) Optional status filter
    if status_filter in ["pending", "in_progress", "completed", "cancelled"]:
        query = query.filter_by(status=status_filter)

    # 3) Optional search filter (search job name or job number)
    if search_query:
        # Simple example: filter by job_number or name, case-insensitive
        query = query.filter(
            db.or_(
                func.lower(Job.job_number).contains(search_query),
                func.lower(Job.name).contains(search_query),
            )
        )

    # 4) Order by most recently updated
    all_jobs = query.order_by(Job.updated_at.desc()).all()

    # Collect client IDs from the jobs to fetch their details
    client_ids = [job.client_id for job in all_jobs]
    clients = {
        client.id: client
        for client in Client.query.filter(Client.id.in_(client_ids)).all()
    }

    # Build a final data structure for the template
    jobs_data = []
    for job in all_jobs:
        client = clients.get(job.client_id)
        jobs_data.append(
            {
                "job_id": job.id,
                "job_number": job.job_number,
                "name": job.name,
                "client_id": client.id if client else None,
                "client_name": client.name if client else "Unknown",
                "client_phone": client.phone if client else None,
                # If you have these properties in your Job model
                "site_confirmation": job.site_confirmation_status,
                "pre_installation": job.pre_installation_status,
                "post_installation": job.post_installation_status,
                "status": job.status,
                "updated_at": job.updated_at,
            }
        )

    return render_template(
        "all_jobs.html",
        jobs=jobs_data,
        search_query=search_query,
        status_filter=status_filter,
        user_role=user_role,
    )


@app.route("/edit-job/<int:job_id>", methods=["GET", "POST"])
def edit_job(job_id):
    if "user_id" not in session:
        flash("Please log in to edit the job.", "error")
        return redirect(url_for("index"))

    user_id = session["user_id"]
    role = session.get("role", "sales")

    # If admin, can edit any job; if sales, only their own
    if role == "admin":
        job = Job.query.get_or_404(job_id)
    else:
        job = Job.query.filter_by(id=job_id, user_id=user_id).first()
        if not job:
            flash("Job not found or access denied.", "error")
            return redirect(url_for("dashboard"))

    if request.method == "POST":
        job_number = request.form.get("job_number", "").strip()
        name = request.form.get("name", "").strip()
        status = request.form.get("status", "pending").strip()
        client_id = request.form.get("client_id")
        address = request.form.get("address", "").strip()
        description = request.form.get("description", "").strip()

        # Get opportunity data from form
        property_type = request.form.get("property_type", "").strip()
        timeframe = request.form.get("timeframe", "").strip()
        num_windows = request.form.get("num_windows")
        num_doors = request.form.get("num_doors")
        hoa_status = request.form.get("hoa_status", "").strip()
        hoa_name = request.form.get("hoa_name", "").strip()
        message = request.form.get("message", "").strip()
        opportunity_source = request.form.get("opportunity_source", "").strip()
        contact_type = request.form.get("contact_type", "").strip()
        pipeline_stage = request.form.get("pipeline_stage", "").strip()

        # Handle service_types and roof_type as multi-select or comma-separated values
        service_types = request.form.getlist("service_types") or []
        if not service_types and request.form.get("service_types_text"):
            service_types = [
                s.strip()
                for s in request.form.get("service_types_text").split(",")
                if s.strip()
            ]

        roof_type = request.form.getlist("roof_type") or []
        if not roof_type and request.form.get("roof_type_text"):
            roof_type = [
                r.strip()
                for r in request.form.get("roof_type_text").split(",")
                if r.strip()
            ]

        if not name:
            flash("Job name is required.", "error")
            return redirect(url_for("edit_job", job_id=job_id))

        # Update basic job fields
        job.job_number = job_number
        job.name = name
        job.status = status
        job.description = description
        job.address = address

        # Update opportunity data fields
        job.property_type = property_type
        job.timeframe = timeframe
        job.hoa_status = hoa_status
        job.hoa_name = hoa_name
        job.message = message
        job.opportunity_source = opportunity_source
        job.contact_type = contact_type
        job.pipeline_stage = pipeline_stage
        job.service_types = service_types
        job.roof_type = roof_type

        # Update numeric fields with validation
        try:
            job.num_windows = (
                int(num_windows) if num_windows and num_windows.strip() else None
            )
        except ValueError:
            flash("Invalid value for number of windows.", "warning")

        try:
            job.num_doors = int(num_doors) if num_doors and num_doors.strip() else None
        except ValueError:
            flash("Invalid value for number of doors.", "warning")

        # If changing the client
        if client_id:
            if role == "admin":
                job.client_id = int(client_id)
            else:
                # Ensure this client belongs to the user
                valid_client = Client.query.filter_by(
                    id=client_id, user_id=user_id
                ).first()
                if not valid_client:
                    flash("Invalid client or access denied.", "error")
                    return redirect(url_for("edit_job", job_id=job_id))
                job.client_id = int(client_id)

        # If admin, check if they assigned to another user
        if role == "admin":
            assigned_user_id = request.form.get("assigned_user_id")
            if assigned_user_id:
                # Validate that user
                assigned_user = User.query.filter_by(id=assigned_user_id).first()
                if assigned_user and assigned_user.role.name == "sales":
                    job.user_id = assigned_user.id
                else:
                    flash("Invalid assigned user.", "error")
                    return redirect(url_for("edit_job", job_id=job_id))

        try:
            db.session.commit()
            flash("Job updated successfully.", "success")
            return redirect(url_for("view_job", job_id=job_id))
        except Exception as e:
            db.session.rollback()
            flash("Error updating job: " + str(e), "error")
            return redirect(url_for("edit_job", job_id=job_id))

    # On GET request
    if role == "admin":
        clients_list = Client.query.order_by(Client.name.asc()).all()
        sales_users = (
            User.query.join(Role)
            .filter(Role.name == "sales", User.is_active == True)
            .order_by(User.name.asc())
            .all()
        )
    else:
        clients_list = (
            Client.query.filter_by(user_id=user_id).order_by(Client.name.asc()).all()
        )
        sales_users = []  # Non-admin can't assign

    # Prepare service types and property types for dropdowns
    service_type_options = [
        "Windows",
        "Doors",
        "Roof",
        "Siding",
        "Gutters",
        "Solar",
        "Other",
    ]
    property_type_options = [
        "Single Family Home",
        "Condo",
        "Townhouse",
        "Multi-Family",
        "Commercial",
        "Other",
    ]
    timeframe_options = ["ASAP", "1-3 Weeks", "1-2 Months", "3-6 Months", "6+ Months"]
    roof_type_options = ["Asphalt", "Metal", "Tile", "Flat", "Wood", "Other"]

    return render_template(
        "edit_job.html",
        job=job,
        clients=clients_list,
        sales_users=sales_users,
        service_type_options=service_type_options,
        property_type_options=property_type_options,
        timeframe_options=timeframe_options,
        roof_type_options=roof_type_options,
    )


@app.route("/view-job/<int:job_id>")
def view_job(job_id):
    # Ensure the user is logged in
    if "user_id" not in session:
        flash("Please log in to view the job.", "error")
        return redirect(url_for("login"))

    # Check user role
    role = session.get("role")
    user_id = session["user_id"]

    # Let admins see any job; let others see only their own.
    if role == "admin":
        job = Job.query.get(job_id)
    else:
        job = Job.query.filter_by(id=job_id, user_id=user_id).first()

    if not job:
        flash("Job not found or access denied.", "error")
        return redirect(url_for("dashboard"))

    # Optionally, fetch any reports
    reports = Report.query.filter_by(job_id=job_id).all()

    # Fetch all job statuses for this job
    job_statuses = JobStatus.query.filter_by(job_id=job_id).all()
    status_dict = {status.stage: status for status in job_statuses}

    # Get job documents
    documents = (
        JobDocument.query.filter_by(job_id=job_id)
        .order_by(JobDocument.uploaded_at.desc())
        .all()
    )

    # Format opportunity data for display
    opportunity_data = {}
    if job.opportunity_data:
        # Extract important fields for display
        opportunity_data = {
            "Source": job.opportunity_source,
            "Contact Type": job.contact_type,
            "Pipeline Stage": job.pipeline_stage,
            "Property Type": job.property_type,
            "Has HOA": "Yes" if job.has_hoa else "No",
            "HOA Name": job.hoa_name if job.hoa_name else "N/A",
            "Timeframe": job.timeframe,
            "Windows": job.num_windows,
            "Doors": job.num_doors,
            "Service Types": (
                ", ".join(job.service_types) if job.service_types else "None"
            ),
            "Roof Type": ", ".join(job.roof_type) if job.roof_type else "None",
        }

    # Render the view job details template with all job information
    return render_template(
        "view_job.html",
        job=job,
        reports=reports,
        documents=documents,
        status_dict=status_dict,
        opportunity_data=opportunity_data,
    )


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

            # Redirect to measure_labor for the actual report content
            flash(
                "Report created successfully. Now add measurements and create an estimate.",
                "success",
            )
            return redirect(url_for("measure_labor", report_id=report.id))
        except Exception as e:
            db.session.rollback()
            flash("Error creating report: " + str(e), "error")

    # Get the client info for the job
    client = Client.query.get(job.client_id) if job.client_id else None

    # Render the report creation form template
    return render_template("create_report.html", job=job, client=client)


@app.route("/job_timeline/<int:job_id>")
def job_timeline(job_id):
    """View a job's timeline including status changes and reports"""
    if "user_id" not in session:
        flash("Please log in to view the job timeline.", "error")
        return redirect(url_for("login"))

    # Verify job exists
    job = Job.query.get_or_404(job_id)
    if job.user_id != session["user_id"] and session.get("role") != "admin":
        flash("Job not found or access denied.", "error")
        return redirect(url_for("dashboard"))

    # Get job status entries
    statuses = (
        JobStatus.query.filter_by(job_id=job_id).order_by(JobStatus.created_at).all()
    )

    # Get reports
    reports = Report.query.filter_by(job_id=job_id).order_by(Report.created_at).all()

    # Merge statuses and reports into a timeline
    timeline_items = []

    # Add statuses
    for status in statuses:
        timeline_items.append(
            {"type": "status", "date": status.created_at, "data": status}
        )

    # Add reports
    for report in reports:
        timeline_items.append(
            {"type": "report", "date": report.created_at, "data": report}
        )

    # Sort by date
    timeline_items.sort(key=lambda x: x["date"])

    return render_template("job_timeline.html", job=job, timeline_items=timeline_items)


@app.route("/profile")
def profile():
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))
    return render_template("profile.html")


# -----------------------------------------------------------
#  Landing page – /pre_installation   (pick a job first)
# -----------------------------------------------------------
@app.route("/pre_installation")
def pre_installation_landing():
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    user_id = session["user_id"]
    role = session.get("role")

    jobs_query = Job.query if role == "admin" else Job.query.filter_by(user_id=user_id)
    jobs = jobs_query.order_by(Job.updated_at.desc()).all()

    return render_template("pre_installation_landing.html", jobs=jobs)


@app.route("/measure_labor", methods=["GET"])
def measure_labor():
    job = None
    client = None

    job_id = request.args.get("job_id")
    report_id = request.args.get("report_id")

    if report_id:
        report = Report.query.get_or_404(report_id)
        job = report.job
    elif job_id:
        job = Job.query.get_or_404(job_id)

    if job:
        client = Client.query.get(job.client_id) if job.client_id else None

    return render_template("measure_labor.html", job=job, client=client)


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


# 3) Clients (Admin & Sales)
@app.route("/clients")
def clients():
    """
    Show all clients to admin;
    sales users only see their own clients.
    """
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    user_id = session["user_id"]
    role = session.get("role")

    # If admin, show all clients; if sales, only the logged-in user’s.
    if role == "admin":
        all_clients = Client.query.all()
    else:
        all_clients = Client.query.filter_by(user_id=user_id).all()

    return render_template("clients.html", clients=all_clients, role=role)


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

        # Check for job_id in the request
        job_id = data.get("job_id")
        logger.info(f"Received job_id: {job_id}")

        # Validate job_id if present
        if job_id:
            job = Job.query.get(job_id)
            if not job:
                logger.error(f"Job ID {job_id} not found")
                return jsonify({"error": f"Job ID {job_id} not found"}), 404

            # Check if user has permission for this job
            if job.user_id != user_id and session.get("role") != "admin":
                logger.error(f"User {user_id} doesn't have permission for job {job_id}")
                return jsonify({"error": "You don't have permission for this job"}), 403

            logger.info(f"Validated job: {job.job_number} for user {user_id}")

        # Create a new report within a transaction
        try:
            # Start transaction by creating the report
            report = Report(user_id=user_id, job_id=job_id)
            db.session.add(report)
            db.session.flush()  # Get the ID without committing yet
            logger.info(
                f"Created report with ID: {report.id}, linked to job_id: {job_id}"
            )

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

            # If job_id is present, update job status if needed
            if job_id:
                job = Job.query.get(job_id)
                if job.status == "pending":
                    job.status = "in_progress"
                    logger.info(f"Updated job {job_id} status to in_progress")

            # If we've made it here, commit the transaction
            db.session.commit()
            logger.info(f"Report {report.id} saved successfully")

            # Return success response
            response_data = {
                "success": True,
                "message": "Report saved successfully!",
                "report_id": report.id,
            }

            # Add job info to response if available
            if job_id:
                response_data["job_id"] = job_id
                response_data["job_number"] = job.job_number

            return jsonify(response_data)

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


@app.route("/api/user_jobs", methods=["GET"])
def user_jobs_api():
    """API endpoint to get a user's jobs for dropdowns"""
    if "user_id" not in session:
        return jsonify({"error": "Not authenticated"}), 401

    user_id = session["user_id"]
    role = session.get("role")

    # For admin, show all jobs. For others, show only their jobs
    if role == "admin":
        jobs = Job.query.order_by(Job.job_number).all()
    else:
        jobs = Job.query.filter_by(user_id=user_id).order_by(Job.job_number).all()

    jobs_data = []
    for job in jobs:
        jobs_data.append(
            {
                "id": job.id,
                "job_number": job.job_number,
                "name": job.name,
            }
        )

    return jsonify({"jobs": jobs_data})


# -----------------------------------------------------------
# GHL Webhook
# -----------------------------------------------------------
from sqlalchemy import func  # add this at the top if not already imported


def extract_contact_info(data):
    """Extract basic contact information from webhook data."""
    name = (
        data.get("full_name")
        or f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
    )
    phone = data.get("phone")
    email = data.get("email")
    address = data.get("Job Address:", "")

    return {"name": name, "phone": phone, "email": email, "address": address}


def extract_job_info(data):
    """Extract job related information from webhook data."""
    job_info = {
        # Basic job info
        "job_name": data.get("opportunity_name", "New Job"),
        "description": f"Pipeline: {data.get('pipeline_name', 'Unknown')}, Status: {data.get('status', 'N/A')}",
        # Service details
        "service_types": data.get("What Services?", []),
        "timeframe": data.get("When Do You Need Service?", ""),
        "property_type": data.get("Property Type", ""),
        # HOA information
        "hoa_status": data.get("Do you live in and HOA?", ""),
        "hoa_name": data.get("HOA Name?", ""),
        # Lead/opportunity metadata
        "opportunity_source": data.get("source", ""),
        "contact_type": data.get("contact_type", ""),
        "pipeline_stage": data.get("pipleline_stage", ""),
        # Additional details
        "address": data.get("Job Address:", ""),
        "num_windows": data.get("How many windows"),
        "num_doors": data.get("How many doors"),
        "roof_type": data.get("Roof type", []),
        "message": data.get("Message", ""),
    }

    return job_info


def get_or_create_user(data):
    """Find existing user or create a new one if not found."""
    # Assigned user email from GHL
    assigned_user_email = data.get("user", {}).get("email")
    if not assigned_user_email:
        raise ValueError("Missing assigned user email")

    assigned_user_email = assigned_user_email.lower()

    # Case-insensitive email lookup
    user = User.query.filter(func.lower(User.email) == assigned_user_email).first()

    if not user:
        logger.warning(f"Assigned user not found: {assigned_user_email}")
        sales_role = Role.query.filter_by(name="sales").first()

        if not sales_role:
            logger.error("Sales role not found. Cannot assign role to new user.")
            raise ValueError("Sales role not defined in system")

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
            is_active=False,
            must_change_password=True,
        )
        db.session.add(user)
        db.session.flush()
        logger.info(f"Auto-created inactive user: {user.email}")

    return user


def create_client(contact_info, user_id):
    """Create a client record with basic contact information."""
    client = Client(
        name=contact_info["name"],
        phone=contact_info["phone"],
        email=contact_info["email"],
        address=contact_info["address"],
        user_id=user_id,
    )
    db.session.add(client)
    db.session.flush()
    return client


def generate_job_number():
    """Generate the next sequential job number."""
    latest_job = Job.query.order_by(Job.id.desc()).first()
    return f"JOB-{latest_job.id + 1:05d}" if latest_job else "JOB-00001"


def create_job(job_info, client_id, user_id, opportunity_data):
    """Create a job with all opportunity data."""
    job_number = generate_job_number()

    # Create job with extended fields
    job = Job(
        job_number=job_number,
        name=job_info["job_name"],
        client_id=client_id,
        user_id=user_id,
        description=job_info["description"],
        # Store opportunity data
        opportunity_data=opportunity_data,
        address=job_info["address"],
        service_types=job_info["service_types"],
        timeframe=job_info["timeframe"],
        property_type=job_info["property_type"],
        hoa_status=job_info["hoa_status"],
        hoa_name=job_info["hoa_name"],
        opportunity_source=job_info["opportunity_source"],
        contact_type=job_info["contact_type"],
        pipeline_stage=job_info["pipeline_stage"],
        # Additional details
        num_windows=job_info["num_windows"],
        num_doors=job_info["num_doors"],
        roof_type=job_info["roof_type"],
        message=job_info["message"],
    )
    db.session.add(job)
    db.session.flush()

    # Create job status stages
    for stage in ["site_confirmation", "pre_installation", "post_installation"]:
        job_status = JobStatus(job_id=job.id, stage=stage, status="incomplete")
        db.session.add(job_status)

    return job


@app.route("/webhook/opportunity", methods=["POST"])
def receive_ghl_opportunity():
    try:
        # Verify content type
        if not request.is_json:
            logger.warning("Request content-type is not application/json")
            return jsonify({"error": "Expected JSON data"}), 400

        # Get and log webhook data
        data = request.get_json()
        logger.info(f"Received GHL opportunity webhook data: {data}")

        # Extract basic information
        contact_info = extract_contact_info(data)
        job_info = extract_job_info(data)

        # Validate basic required data
        if not contact_info["name"]:
            logger.warning("Missing contact name")
            return jsonify({"error": "Missing contact name"}), 400

        # Process the data in transaction
        try:
            # Get or create user
            user = get_or_create_user(data)

            # Create client
            client = create_client(contact_info, user.id)

            # Create job with all opportunity data
            job = create_job(job_info, client.id, user.id, data)

            # Commit transaction
            db.session.commit()
            logger.info(
                f"Created client '{client.name}', job '{job.job_number}' assigned to '{user.email}'"
            )

            return (
                jsonify(
                    {
                        "message": "Client, job, and statuses created successfully",
                        "job_number": job.job_number,
                        "client_id": client.id,
                        "services": (
                            job.services_summary
                            if hasattr(job, "services_summary")
                            else ", ".join(job_info["service_types"])
                        ),
                    }
                ),
                200,
            )
        except ValueError as ve:
            db.session.rollback()
            logger.error(f"Validation error: {str(ve)}")
            return jsonify({"error": str(ve)}), 400

    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        logger.error(traceback.format_exc())
        db.session.rollback()
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


# -----------------------------------------------------------
# Rports
# -----------------------------------------------------------


@app.route("/reports")
def reports():
    """View all reports"""
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    user_id = session["user_id"]
    role = session.get("role")

    # For admin, show all reports. For others, show only their reports
    if role == "admin":
        all_reports = Report.query.order_by(Report.created_at.desc()).all()
    else:
        all_reports = (
            Report.query.filter_by(user_id=user_id)
            .order_by(Report.created_at.desc())
            .all()
        )

    # Collect related data
    report_ids = [report.id for report in all_reports]

    # Get measurements counts
    measurements_counts = {}
    for report_id in report_ids:
        count = Measurement.query.filter_by(report_id=report_id).count()
        measurements_counts[report_id] = count

    # Get estimate data
    estimates = {}
    for estimate in Estimate.query.filter(Estimate.report_id.in_(report_ids)).all():
        estimates[estimate.report_id] = estimate

    # Get job information if available
    job_info = {}
    job_ids = [report.job_id for report in all_reports if report.job_id]
    jobs = {job.id: job for job in Job.query.filter(Job.id.in_(job_ids)).all()}

    for report in all_reports:
        if report.job_id and report.job_id in jobs:
            job = jobs[report.job_id]
            job_info[report.id] = {"job_number": job.job_number, "job_name": job.name}

    # Get user information for admins
    users = {}
    if role == "admin":
        user_ids = [report.user_id for report in all_reports]
        for user in User.query.filter(User.id.in_(user_ids)).all():
            users[user.id] = user.name

    # This return statement was missing
    return render_template(
        "reports.html",
        reports=all_reports,
        measurements_counts=measurements_counts,
        estimates=estimates,
        job_info=job_info,
        users=users,
        is_admin=(role == "admin"),
    )


@app.route("/view_report/<int:report_id>")
def view_report(report_id):
    """View a specific report's details"""
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    user_id = session["user_id"]
    role = session.get("role")

    # Get the report
    report = Report.query.get_or_404(report_id)

    # Check permission: admins can view all reports, others only their own
    if role != "admin" and report.user_id != user_id:
        flash("You do not have permission to view this report.", "error")
        return redirect(url_for("reports"))

    # Get all measurements for this report
    measurements = (
        Measurement.query.filter_by(report_id=report_id).order_by(Measurement.nbr).all()
    )

    # Get the estimate
    estimate = Estimate.query.filter_by(report_id=report_id).first()

    # Get job information if available
    job = None
    if report.job_id:
        job = Job.query.get(report.job_id)

    # Get report creator information
    creator = User.query.get(report.user_id)

    return render_template(
        "view_report.html",
        report=report,
        measurements=measurements,
        estimate=estimate,
        job=job,
        creator=creator,
    )


@app.route("/delete_report/<int:report_id>", methods=["POST"])
def delete_report(report_id):
    """Delete a report"""
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    user_id = session["user_id"]
    role = session.get("role")

    # Get the report
    report = Report.query.get_or_404(report_id)

    # Check permission: admins can delete any report, others only their own
    if role != "admin" and report.user_id != user_id:
        flash("You do not have permission to delete this report.", "error")
        return redirect(url_for("reports"))

    try:
        # Delete associated measurements
        Measurement.query.filter_by(report_id=report_id).delete()

        # Delete associated estimate
        Estimate.query.filter_by(report_id=report_id).delete()

        # Delete the report
        db.session.delete(report)
        db.session.commit()

        flash("Report deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting report: {str(e)}", "error")

    return redirect(url_for("reports"))


@app.route("/api/reports/data")
def reports_data_api():
    """API endpoint to get reports data for AJAX calls"""
    if "user_id" not in session:
        return jsonify({"error": "Not authenticated"}), 401

    user_id = session["user_id"]
    role = session.get("role")

    # For admin, show all reports. For others, show only their reports
    if role == "admin":
        all_reports = Report.query.order_by(Report.created_at.desc()).all()
    else:
        all_reports = (
            Report.query.filter_by(user_id=user_id)
            .order_by(Report.created_at.desc())
            .all()
        )

    # Format data for response
    reports_data = []

    for report in all_reports:
        # Get estimate if available
        estimate = Estimate.query.filter_by(report_id=report.id).first()
        total_contract = estimate.total_contract if estimate else 0

        # Get measurements count
        measurements_count = Measurement.query.filter_by(report_id=report.id).count()

        # Get job info if available
        job_info = None
        if report.job_id:
            job = Job.query.get(report.job_id)
            if job:
                job_info = {"job_number": job.job_number, "job_name": job.name}

        # Get creator info if admin
        creator_name = None
        if role == "admin":
            creator = User.query.get(report.user_id)
            creator_name = creator.name if creator else "Unknown"

        report_data = {
            "id": report.id,
            "created_at": report.created_at.strftime("%Y-%m-%d %H:%M"),
            "measurements_count": measurements_count,
            "total_contract": total_contract,
            "job_info": job_info,
            "creator_name": creator_name,
        }

        reports_data.append(report_data)

    return jsonify({"reports": reports_data})


# Add these routes to your app.py file


@app.route("/edit_report/<int:report_id>", methods=["GET", "POST"])
def edit_report(report_id):
    """Edit a specific report - admin only"""
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    # Check if the user is an admin
    if session.get("role") != "admin":
        flash("Only administrators can edit reports.", "error")
        return redirect(url_for("reports"))

    # Get the report
    report = Report.query.get_or_404(report_id)

    if request.method == "POST":
        try:
            # Process estimate data
            estimate = Estimate.query.filter_by(report_id=report_id).first()
            if estimate:
                # Helper function to safely convert values
                def safe_convert(value, default=0):
                    if value is None:
                        return default
                    try:
                        return float(value)
                    except (ValueError, TypeError):
                        return default

                # Update estimate fields
                estimate.extra_large_qty = int(
                    safe_convert(request.form.get("extra_large_qty"))
                )
                estimate.large_qty = int(safe_convert(request.form.get("large_qty")))
                estimate.small_qty = int(safe_convert(request.form.get("small_qty")))
                estimate.mull_qty = int(safe_convert(request.form.get("mull_qty")))
                estimate.sfd_qty = int(safe_convert(request.form.get("sfd_qty")))
                estimate.dfd_qty = int(safe_convert(request.form.get("dfd_qty")))
                estimate.sgd_qty = int(safe_convert(request.form.get("sgd_qty")))
                estimate.extra_panels_qty = int(
                    safe_convert(request.form.get("extra_panels_qty"))
                )
                estimate.door_design_qty = int(
                    safe_convert(request.form.get("door_design_qty"))
                )
                estimate.shutter_removal_qty = int(
                    safe_convert(request.form.get("shutter_removal_qty"))
                )
                estimate.labor_total = safe_convert(request.form.get("labor_total"))
                estimate.marketing_fee = safe_convert(request.form.get("marketing_fee"))
                estimate.material_cost = safe_convert(request.form.get("material_cost"))
                estimate.markup = safe_convert(request.form.get("markup"), 5000)

                # Calculate derived fields
                estimate.salesman_cost = (
                    estimate.labor_total
                    + estimate.marketing_fee
                    + estimate.material_cost
                )
                estimate.total_contract = estimate.salesman_cost + estimate.markup
                estimate.commission = estimate.markup * 0.2

            # Process measurements
            # First, handle deletion of measurements if requested
            if "delete_measurements" in request.form:
                measurement_ids = request.form.getlist("delete_measurements")
                for m_id in measurement_ids:
                    measurement = Measurement.query.get(int(m_id))
                    if measurement and measurement.report_id == report_id:
                        db.session.delete(measurement)

            # Update existing measurements
            for measurement in report.measurements:
                prefix = f"measurement_{measurement.id}_"
                if prefix + "style" in request.form:
                    measurement.style = request.form.get(prefix + "style")
                    measurement.config = request.form.get(prefix + "config", "")

                    # Handle numeric values
                    try:
                        width = request.form.get(prefix + "width")
                        height = request.form.get(prefix + "height")
                        measurement.width = (
                            float(width) if width and width.strip() else None
                        )
                        measurement.height = (
                            float(height) if height and height.strip() else None
                        )
                    except ValueError:
                        flash(
                            f"Invalid numeric value for measurement #{measurement.nbr}",
                            "error",
                        )

                    # Handle boolean values
                    measurement.door_design = (
                        request.form.get(prefix + "door_design") == "Yes"
                    )
                    measurement.priv = request.form.get(prefix + "priv") == "Yes"
                    measurement.eg = request.form.get(prefix + "eg") == "Yes"
                    measurement.grids = request.form.get(prefix + "grids") == "Yes"
                    measurement.grid_config = request.form.get(
                        prefix + "grid_config", ""
                    )
                    measurement.sr = request.form.get(prefix + "sr") == "Yes"

            # Add new measurements if requested
            if "new_measurement_count" in request.form:
                new_count = int(request.form.get("new_measurement_count", 0))
                for i in range(1, new_count + 1):
                    prefix = f"new_measurement_{i}_"
                    if prefix + "style" in request.form:
                        style = request.form.get(prefix + "style")
                        if not style:
                            continue  # Skip if no style is selected

                        # Get other values
                        config = request.form.get(prefix + "config", "")

                        # Handle numeric values
                        try:
                            width = request.form.get(prefix + "width")
                            height = request.form.get(prefix + "height")
                            width_val = (
                                float(width) if width and width.strip() else None
                            )
                            height_val = (
                                float(height) if height and height.strip() else None
                            )
                        except ValueError:
                            flash(
                                f"Invalid numeric value for new measurement #{i}",
                                "error",
                            )
                            continue

                        # Handle boolean values
                        door_design = request.form.get(prefix + "door_design") == "Yes"
                        priv = request.form.get(prefix + "priv") == "Yes"
                        eg = request.form.get(prefix + "eg") == "Yes"
                        grids = request.form.get(prefix + "grids") == "Yes"
                        grid_config = request.form.get(prefix + "grid_config", "")
                        sr = request.form.get(prefix + "sr") == "Yes"

                        # Create new measurement
                        nbr = len(report.measurements) + i
                        new_measurement = Measurement(
                            report_id=report_id,
                            nbr=nbr,
                            style=style,
                            config=config,
                            width=width_val,
                            height=height_val,
                            door_design=door_design,
                            priv=priv,
                            eg=eg,
                            grids=grids,
                            grid_config=grid_config,
                            sr=sr,
                        )
                        db.session.add(new_measurement)

            # Commit all changes
            db.session.commit()
            flash("Report updated successfully!", "success")
            return redirect(url_for("view_report", report_id=report_id))

        except Exception as e:
            db.session.rollback()
            flash(f"Error updating report: {str(e)}", "error")

    # GET request - show edit form
    # Get all measurements for this report
    measurements = (
        Measurement.query.filter_by(report_id=report_id).order_by(Measurement.nbr).all()
    )

    # Get the estimate
    estimate = Estimate.query.filter_by(report_id=report_id).first()

    # Get job information if available
    job = None
    if report.job_id:
        job = Job.query.get(report.job_id)

    # Get report creator information
    creator = User.query.get(report.user_id)

    # Return the template with all necessary data
    return render_template(
        "edit_report.html",
        report=report,
        measurements=measurements,
        estimate=estimate,
        job=job,
        creator=creator,
    )


@app.route("/export_report/<int:report_id>/excel")
def export_report_excel(report_id):
    """Export a report as Excel"""
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    # Get the report
    report = Report.query.get_or_404(report_id)

    # Get all measurements for this report
    measurements = (
        Measurement.query.filter_by(report_id=report_id).order_by(Measurement.nbr).all()
    )

    # Get the estimate
    estimate = Estimate.query.filter_by(report_id=report_id).first()

    # Get report creator information
    creator = User.query.get(report.user_id)

    try:
        import xlsxwriter
        import tempfile

        # Create a temporary file
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            temp_file = f.name

        # Create a workbook and add worksheets
        workbook = xlsxwriter.Workbook(temp_file)

        # Add formatting
        header_format = workbook.add_format(
            {
                "bold": True,
                "align": "center",
                "bg_color": "#3498db",
                "font_color": "white",
                "border": 1,
                "pattern": 1,  # <-- Important so that bg_color is actually applied
            }
        )

        cell_format = workbook.add_format({"border": 1})

        money_format = workbook.add_format({"border": 1, "num_format": "$#,##0.00"})

        # Report Info worksheet
        info_sheet = workbook.add_worksheet("Report Info")
        info_sheet.write(0, 0, "Report Information", header_format)
        info_sheet.merge_range("A1:B1", "Report Information", header_format)

        # Basic report information
        info_sheet.write(1, 0, "Report ID:", cell_format)
        info_sheet.write(1, 1, report.id, cell_format)

        info_sheet.write(2, 0, "Created At:", cell_format)
        info_sheet.write(
            2, 1, report.created_at.strftime("%m/%d/%Y %I:%M %p"), cell_format
        )

        info_sheet.write(3, 0, "Created By:", cell_format)
        info_sheet.write(3, 1, creator.name, cell_format)

        # Job information if available
        row = 5
        if report.job_id:
            job = Job.query.get(report.job_id)
            if job:
                info_sheet.write(row, 0, "Job Number:", cell_format)
                info_sheet.write(row, 1, job.job_number, cell_format)
                row += 1

                info_sheet.write(row, 0, "Job Name:", cell_format)
                info_sheet.write(row, 1, job.name, cell_format)
                row += 1

                if hasattr(job, "client") and job.client:
                    info_sheet.write(row, 0, "Client:", cell_format)
                    info_sheet.write(row, 1, job.client.name, cell_format)
        else:
            info_sheet.write(row, 0, "Job:", cell_format)
            info_sheet.write(row, 1, "Not associated with a job", cell_format)

        # Set column widths
        info_sheet.set_column("A:A", 15)
        info_sheet.set_column("B:B", 30)

        # Measurements worksheet
        if measurements:
            meas_sheet = workbook.add_worksheet("Measurements")

            # Headers
            headers = [
                "Nbr.",
                "Style",
                "CONFIG",
                "W",
                "H",
                "Door Design",
                "PRIV",
                "EG",
                "Grids",
                "Grid Config.",
                "S/R",
            ]

            for col, header in enumerate(headers):
                meas_sheet.write(0, col, header, header_format)

            # Data
            for row, measurement in enumerate(measurements, start=1):
                meas_sheet.write(row, 0, measurement.nbr, cell_format)
                meas_sheet.write(row, 1, measurement.style, cell_format)
                meas_sheet.write(row, 2, measurement.config, cell_format)
                meas_sheet.write(row, 3, measurement.width, cell_format)
                meas_sheet.write(row, 4, measurement.height, cell_format)
                meas_sheet.write(
                    row, 5, "Yes" if measurement.door_design else "No", cell_format
                )
                meas_sheet.write(
                    row, 6, "Yes" if measurement.priv else "No", cell_format
                )
                meas_sheet.write(row, 7, "Yes" if measurement.eg else "No", cell_format)
                meas_sheet.write(
                    row, 8, "Yes" if measurement.grids else "No", cell_format
                )
                meas_sheet.write(row, 9, measurement.grid_config, cell_format)
                meas_sheet.write(
                    row, 10, "Yes" if measurement.sr else "No", cell_format
                )

            # Set column widths
            meas_sheet.set_column("A:A", 5)  # Nbr
            meas_sheet.set_column("B:B", 8)  # Style
            meas_sheet.set_column("C:C", 15)  # CONFIG
            meas_sheet.set_column("D:D", 5)  # W
            meas_sheet.set_column("E:E", 5)  # H
            meas_sheet.set_column("F:F", 12)  # Door Design
            meas_sheet.set_column("G:G", 6)  # PRIV
            meas_sheet.set_column("H:H", 6)  # EG
            meas_sheet.set_column("I:I", 8)  # Grids
            meas_sheet.set_column("J:J", 15)  # Grid Config
            meas_sheet.set_column("K:K", 6)  # S/R

        # Estimate worksheet
        if estimate:
            est_sheet = workbook.add_worksheet("Estimate")

            # Windows section
            est_sheet.merge_range("A1:D1", "WINDOWS", header_format)
            est_sheet.write(1, 0, "Category", header_format)
            est_sheet.write(1, 1, "Amount", header_format)
            est_sheet.write(1, 2, "QTY", header_format)
            est_sheet.write(1, 3, "Total", header_format)

            row = 2
            est_sheet.write(row, 0, "Extra large 111+", cell_format)
            est_sheet.write(row, 1, 450, money_format)
            est_sheet.write(row, 2, estimate.extra_large_qty, cell_format)
            est_sheet.write(row, 3, estimate.extra_large_qty * 450, money_format)
            row += 1

            est_sheet.write(row, 0, "Large 75-110", cell_format)
            est_sheet.write(row, 1, 360, money_format)
            est_sheet.write(row, 2, estimate.large_qty, cell_format)
            est_sheet.write(row, 3, estimate.large_qty * 360, money_format)
            row += 1

            est_sheet.write(row, 0, "Small 1-74", cell_format)
            est_sheet.write(row, 1, 300, money_format)
            est_sheet.write(row, 2, estimate.small_qty, cell_format)
            est_sheet.write(row, 3, estimate.small_qty * 300, money_format)
            row += 1

            est_sheet.write(row, 0, "Mull Door / Win", cell_format)
            est_sheet.write(row, 1, 40, money_format)
            est_sheet.write(row, 2, estimate.mull_qty, cell_format)
            est_sheet.write(row, 3, estimate.mull_qty * 40, money_format)
            row += 2

            # Doors section
            est_sheet.merge_range(f"A{row}:D{row}", "DOORS", header_format)
            row += 1
            est_sheet.write(row, 0, "Category", header_format)
            est_sheet.write(row, 1, "Amount", header_format)
            est_sheet.write(row, 2, "QTY", header_format)
            est_sheet.write(row, 3, "Total", header_format)
            row += 1

            est_sheet.write(row, 0, "SFD", cell_format)
            est_sheet.write(row, 1, 825, money_format)
            est_sheet.write(row, 2, estimate.sfd_qty, cell_format)
            est_sheet.write(row, 3, estimate.sfd_qty * 825, money_format)
            row += 1

            est_sheet.write(row, 0, "DFD", cell_format)
            est_sheet.write(row, 1, 900, money_format)
            est_sheet.write(row, 2, estimate.dfd_qty, cell_format)
            est_sheet.write(row, 3, estimate.dfd_qty * 900, money_format)
            row += 1

            est_sheet.write(row, 0, "SGD", cell_format)
            est_sheet.write(row, 1, 600, money_format)
            est_sheet.write(row, 2, estimate.sgd_qty, cell_format)
            est_sheet.write(row, 3, estimate.sgd_qty * 600, money_format)
            row += 1

            est_sheet.write(row, 0, "Extra Panels", cell_format)
            est_sheet.write(row, 1, 225, money_format)
            est_sheet.write(row, 2, estimate.extra_panels_qty, cell_format)
            est_sheet.write(row, 3, estimate.extra_panels_qty * 225, money_format)
            row += 1

            est_sheet.write(row, 0, "Door Design/panel", cell_format)
            est_sheet.write(row, 1, 1050, money_format)
            est_sheet.write(row, 2, estimate.door_design_qty, cell_format)
            est_sheet.write(row, 3, estimate.door_design_qty * 1050, money_format)
            row += 1

            est_sheet.write(row, 0, "Shutter Removal", cell_format)
            est_sheet.write(row, 1, 40, money_format)
            est_sheet.write(row, 2, estimate.shutter_removal_qty, cell_format)
            est_sheet.write(row, 3, estimate.shutter_removal_qty * 40, money_format)
            row += 2

            # Permit section
            est_sheet.merge_range(f"A{row}:D{row}", "PERMIT", header_format)
            row += 1
            est_sheet.write(row, 0, "PERMIT PREP", cell_format)
            est_sheet.write(row, 1, 450, money_format)
            est_sheet.write(row, 2, 1, cell_format)
            est_sheet.write(row, 3, estimate.permit_cost, money_format)
            row += 1

            est_sheet.write(
                row, 0, "LABOR TOTAL", workbook.add_format({"bold": True, "border": 1})
            )
            est_sheet.merge_range(f"B{row}:C{row}", "", cell_format)
            est_sheet.write(row, 3, estimate.labor_total, money_format)
            row += 2

            # Marketing section
            est_sheet.merge_range(f"A{row}:D{row}", "MARKETING", header_format)
            row += 1
            est_sheet.write(row, 0, "Referral/Marketing/Fee", cell_format)
            est_sheet.write(row, 1, "", cell_format)
            est_sheet.write(row, 2, estimate.marketing_fee, cell_format)
            est_sheet.write(row, 3, estimate.marketing_fee, money_format)
            row += 2

            # Material section
            est_sheet.merge_range(f"A{row}:D{row}", "MATERIAL", header_format)
            row += 1
            est_sheet.write(row, 0, "Material Cost", cell_format)
            est_sheet.write(row, 1, "", cell_format)
            est_sheet.write(row, 2, estimate.material_cost, cell_format)
            est_sheet.write(row, 3, estimate.material_cost, money_format)
            row += 1

            est_sheet.write(row, 0, "Salesman Cost", cell_format)
            est_sheet.merge_range(f"B{row}:C{row}", "", cell_format)
            est_sheet.write(row, 3, estimate.salesman_cost, money_format)
            row += 1

            est_sheet.write(row, 0, "Markup", cell_format)
            est_sheet.write(row, 1, "", cell_format)
            est_sheet.write(row, 2, estimate.markup, cell_format)
            est_sheet.write(row, 3, estimate.markup, money_format)
            row += 2

            # Totals
            est_sheet.write(
                row,
                0,
                "TOTAL CONTRACT AMOUNT",
                workbook.add_format({"bold": True, "border": 1}),
            )
            est_sheet.merge_range(f"B{row}:C{row}", "", cell_format)
            est_sheet.write(row, 3, estimate.total_contract, money_format)
            row += 1

            est_sheet.write(row, 0, "COMMISSION", cell_format)
            est_sheet.merge_range(f"B{row}:C{row}", "", cell_format)
            est_sheet.write(row, 3, estimate.commission, money_format)

            # Set column widths
            est_sheet.set_column("A:A", 25)
            est_sheet.set_column("B:B", 10)
            est_sheet.set_column("C:C", 8)
            est_sheet.set_column("D:D", 12)

        # Close the workbook
        workbook.close()

        # Send file to client
        return_data = open(temp_file, "rb").read()
        os.unlink(temp_file)  # Delete temp file

        response = make_response(return_data)
        response.headers["Content-Type"] = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response.headers["Content-Disposition"] = (
            f"attachment; filename=report_{report_id}.xlsx"
        )
        return response

    except Exception as e:
        import traceback

        traceback.print_exc()  # Print full error in console
        flash(f"Error generating Excel file: {str(e)}", "error")
        return redirect(url_for("view_report", report_id=report_id))


@app.route("/export_report/<int:report_id>/pdf")
def export_report_pdf(report_id):
    """Displays a print-friendly version of the report that users can print to PDF"""
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    # Get the report
    report = Report.query.get_or_404(report_id)

    # Get all measurements for this report
    measurements = (
        Measurement.query.filter_by(report_id=report_id).order_by(Measurement.nbr).all()
    )

    # Get the estimate
    estimate = Estimate.query.filter_by(report_id=report_id).first()

    # Get job information if available
    job = None
    if report.job_id:
        job = Job.query.get(report.job_id)

    # Get report creator information
    creator = User.query.get(report.user_id)

    return render_template(
        "print_report.html",
        report=report,
        measurements=measurements,
        estimate=estimate,
        job=job,
        creator=creator,
        hide_nav=True,  # Hide navigation to make it print-friendly
    )


@app.route("/job_reports/<int:job_id>")
def job_reports(job_id):
    """View all reports for a specific job"""
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    # Verify job exists and belongs to the user
    job = Job.query.get_or_404(job_id)
    if job.user_id != session["user_id"] and session.get("role") != "admin":
        flash("You don't have permission to view this job's reports.", "error")
        return redirect(url_for("dashboard"))

    # Get all reports for this job
    reports = (
        Report.query.filter_by(job_id=job_id).order_by(Report.created_at.desc()).all()
    )

    # Get measurements counts
    measurements_counts = {}
    for report in reports:
        count = Measurement.query.filter_by(report_id=report.id).count()
        measurements_counts[report.id] = count

    # Get estimate data
    estimates = {}
    for estimate in Estimate.query.filter(
        Estimate.report_id.in_([r.id for r in reports])
    ).all():
        estimates[estimate.report_id] = estimate

    return render_template(
        "job_reports.html",
        job=job,
        reports=reports,
        measurements_counts=measurements_counts,
        estimates=estimates,
    )


@app.route("/link_report_to_job/<int:report_id>", methods=["POST"])
def link_report_to_job(report_id):
    """Link an existing report to a job"""
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    # Get the report
    report = Report.query.get_or_404(report_id)

    # Check permissions
    if report.user_id != session["user_id"] and session.get("role") != "admin":
        flash("You don't have permission to modify this report.", "error")
        return redirect(url_for("reports"))

    # Get job_id from form
    job_id = request.form.get("job_id")
    if not job_id:
        flash("No job selected.", "error")
        return redirect(url_for("view_report", report_id=report_id))

    # Verify job exists
    job = Job.query.get(job_id)
    if not job:
        flash("Selected job not found.", "error")
        return redirect(url_for("view_report", report_id=report_id))

    # Link report to job
    report.job_id = job_id
    db.session.commit()

    flash(f"Report #{report_id} successfully linked to {job.job_number}.", "success")
    return redirect(url_for("view_report", report_id=report_id))


@app.route("/jobs/<int:job_id>/window_lead_checklist_pdf")
def generate_window_lead_checklist_pdf(job_id):
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    job = Job.query.get_or_404(job_id)
    if session.get("role") != "admin" and job.user_id != session["user_id"]:
        flash("Access denied.", "error")
        return redirect(url_for("dashboard"))

    # Fetch the associated checklist
    checklist = WindowLeadChecklist.query.filter_by(job_id=job_id).first()

    # If no checklist, maybe redirect or create a new one
    if not checklist:
        flash("No Window Lead Checklist found. Please fill it out first.", "info")
        return redirect(url_for("edit_window_lead_checklist", job_id=job_id))

    # path to wkhtmltopdf:
    path_to_wkhtmltopdf = r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe"
    config = pdfkit.configuration(wkhtmltopdf=path_to_wkhtmltopdf)

    # Render template (the same or a separate PDF-friendly version).
    html_content = render_template(
        "window_lead_checklist_template.html",
        job=job,
        checklist=checklist,
        now=datetime.utcnow,
    )

    # Generate PDF
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        pdf_file_path = tmp.name

    pdfkit.from_string(html_content, pdf_file_path, configuration=config)

    return send_file(
        pdf_file_path, as_attachment=True, download_name="window_lead_checklist.pdf"
    )


@app.route("/window_lead_checklist/<int:job_id>", methods=["GET", "POST"])
def edit_window_lead_checklist(job_id):
    """
    Create or edit the WindowLeadChecklist for a given Job.
    """
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    # Check that this job belongs to the user (if not admin)
    job = Job.query.get_or_404(job_id)
    if session.get("role") != "admin" and job.user_id != session["user_id"]:
        flash("Access denied.", "error")
        return redirect(url_for("dashboard"))

    # Try to get existing checklist
    checklist = WindowLeadChecklist.query.filter_by(job_id=job_id).first()
    if not checklist:
        # Create a new one in memory (won't save until we commit)
        checklist = WindowLeadChecklist(job_id=job_id)

    if request.method == "POST":
        # Populate fields from form
        # Booleans
        checklist.material_order = bool(request.form.get("material_order"))
        checklist.property_appraiser_snapshot = bool(
            request.form.get("property_appraiser_snapshot")
        )
        checklist.measure_labor_sheet = bool(request.form.get("measure_labor_sheet"))
        checklist.property_appraiser_building_sketch = bool(
            request.form.get("property_appraiser_building_sketch")
        )
        checklist.property_photos_labeled = bool(
            request.form.get("property_photos_labeled")
        )

        # Date (convert from string if necessary)
        date_str = request.form.get("date")
        if date_str:
            checklist.date = datetime.strptime(date_str, "%Y-%m-%d")

        # Basic strings
        checklist.sales_rep = request.form.get("sales_rep")
        checklist.client_name = request.form.get("client_name")
        checklist.job_address = request.form.get("job_address")

        # HOA
        checklist.hoa_yes = bool(request.form.get("hoa_yes"))
        checklist.hoa_no = bool(request.form.get("hoa_no"))
        checklist.hoa_community_name = request.form.get("hoa_community_name")

        # Payment
        checklist.is_cash = bool(request.form.get("is_cash"))
        checklist.is_finance = bool(request.form.get("is_finance"))
        checklist.finance_type = request.form.get("finance_type")

        # Financing terms
        checklist.term_0_interest = bool(request.form.get("term_0_interest"))
        checklist.term_5_year = bool(request.form.get("term_5_year"))
        checklist.term_10_year = bool(request.form.get("term_10_year"))
        checklist.term_15_year = bool(request.form.get("term_15_year"))
        checklist.term_20_year = bool(request.form.get("term_20_year"))

        checklist.monthly_budget = request.form.get("monthly_budget")

        # Purpose
        checklist.purpose_insurance = bool(request.form.get("purpose_insurance"))
        checklist.purpose_rental = bool(request.form.get("purpose_rental"))
        checklist.purpose_remodel = bool(request.form.get("purpose_remodel"))
        checklist.purpose_house_flip = bool(request.form.get("purpose_house_flip"))
        checklist.purpose_new_construction = bool(
            request.form.get("purpose_new_construction")
        )

        # Horizon
        checklist.horizon_asap = bool(request.form.get("horizon_asap"))
        checklist.horizon_30_days = bool(request.form.get("horizon_30_days"))
        checklist.horizon_2_3_months = bool(request.form.get("horizon_2_3_months"))
        checklist.horizon_woft = bool(request.form.get("horizon_woft"))

        # Notes
        checklist.notes = request.form.get("notes")

        # Enclosure, structural, etc
        checklist.encl_photo_with_sketch = bool(
            request.form.get("encl_photo_with_sketch")
        )
        checklist.encl_notated_areas = bool(request.form.get("encl_notated_areas"))
        checklist.encl_existing_sliding_door_remain = bool(
            request.form.get("encl_existing_sliding_door_remain")
        )
        checklist.building_3_stories = bool(request.form.get("building_3_stories"))
        checklist.link_afceng = request.form.get("link_afceng")
        checklist.structural_modifications = bool(
            request.form.get("structural_modifications")
        )
        checklist.structural_photo_area_drawn = bool(
            request.form.get("structural_photo_area_drawn")
        )
        checklist.structural_photo_in_out = bool(
            request.form.get("structural_photo_in_out")
        )

        # If it's a new checklist, we need to add it to the session
        if checklist.id is None:
            db.session.add(checklist)

        db.session.commit()
        flash("Checklist saved!", "success")
        return redirect(url_for("edit_window_lead_checklist", job_id=job_id))

    # If GET, just render the form
    return render_template(
        "edit_window_lead_checklist.html", job=job, checklist=checklist
    )


@app.template_filter("strftime")
def _jinja2_filter_datetime(date, fmt=None):
    if not date:
        return ""
    return date.strftime(fmt)


@app.route("/jobs/<int:job_id>/upload-document", methods=["POST"])
def upload_job_document(job_id):
    if "user_id" not in session:
        return redirect(url_for("index"))
    user_id = session["user_id"]

    job = Job.query.get_or_404(job_id)
    # If not admin, or not job.user_id, do permission check if needed
    if job.user_id != user_id and session.get("role") != "admin":
        flash("You don't have permission to upload documents to this job.", "error")
        return redirect(url_for("dashboard"))

    # If you want to limit to 10, check existing doc count:
    if len(job.documents) >= 10:
        flash(
            "This job already has 10 documents. Remove some before adding more.",
            "error",
        )
        return redirect(url_for("view_job", job_id=job_id))

    # Grab file from form
    file = request.files.get("document_file")
    title = request.form.get("title", "Uploaded Document")

    if not file:
        flash("No file provided.", "error")
        return redirect(url_for("view_job", job_id=job_id))

    filename = secure_filename(file.filename)
    if filename == "":
        flash("Invalid file name.", "error")
        return redirect(url_for("view_job", job_id=job_id))

    # Create a unique filename to prevent overwriting
    unique_filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{filename}"
    save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_filename)

    try:
        # Ensure the directory exists
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        # Save the file
        file.save(save_path)

        # Create a JobDocument record
        new_doc = JobDocument(
            job_id=job_id,
            user_id=user_id,
            title=title,
            filename=filename,  # Store the original filename for display
            mime_type=file.content_type,
            file_path=save_path,  # Store the full path to the file
        )
        db.session.add(new_doc)
        db.session.commit()

        flash("Document uploaded successfully!", "success")
    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        flash(f"Error uploading document: {str(e)}", "error")

    return redirect(url_for("view_job", job_id=job_id))


@app.route("/documents/<int:document_id>/download")
def download_job_document(document_id):
    doc = JobDocument.query.get_or_404(document_id)

    # Check if the file exists
    if not os.path.exists(doc.file_path):
        flash("Document file not found on the server.", "error")
        return redirect(url_for("view_job", job_id=doc.job_id))

    # Optionally do permission checks
    return send_file(doc.file_path, as_attachment=True, download_name=doc.filename)


@app.route("/documents/<int:document_id>/view")
def view_job_document(document_id):
    doc = JobDocument.query.get_or_404(document_id)

    # Check if the file exists
    if not os.path.exists(doc.file_path):
        flash("Document file not found on the server.", "error")
        return redirect(url_for("view_job", job_id=doc.job_id))

    return send_file(doc.file_path, mimetype=doc.mime_type)


# -----------------------------------------------------------
# Site Confirmation
# -----------------------------------------------------------
# ─────────────────────────────────────────────────────────────
#  Site‑confirmation (landing page **and** job page)
# ─────────────────────────────────────────────────────────────
@app.route("/site_confirmation", defaults={"job_id": None}, methods=["GET"])
@app.route("/job/<int:job_id>/site_confirmation", methods=["GET", "POST"])
def site_confirmation(job_id):
    """
    1.  /site_confirmation
        →  shows a list of jobs the user can choose from.
    2.  /job/<job_id>/site_confirmation
        →  shows the full site‑confirmation form for that job,
           including document upload / deletion, completion, etc.
    """
    # ────────────────────────────
    #  Authentication
    # ────────────────────────────
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    user_id = session["user_id"]
    user_role = session.get("role")

    # ────────────────────────────
    #  CASE A – landing page
    # ────────────────────────────
    if job_id is None:
        # Admin sees all jobs; sales see only their own
        if user_role == "admin":
            available_jobs = Job.query.order_by(Job.updated_at.desc()).all()
        else:
            available_jobs = (
                Job.query.filter_by(user_id=user_id)
                .order_by(Job.updated_at.desc())
                .all()
            )

        return render_template(
            "site_confirmation_landing.html",
            jobs=available_jobs,
        )

    # ────────────────────────────
    #  CASE B – specific job page
    # ────────────────────────────
    # 1.  Access control
    # ----------------------------------------------------------
    job = Job.query.get_or_404(job_id)
    if job.user_id != user_id and user_role != "admin":
        flash("You don't have permission to access this job.", "error")
        return redirect(url_for("dashboard"))

    # 2.  Get or create the SiteConfirmation record
    # ----------------------------------------------------------
    site_confirmation = SiteConfirmation.query.filter_by(job_id=job_id).first()
    if not site_confirmation:
        site_confirmation = SiteConfirmation(job_id=job_id)
        db.session.add(site_confirmation)
        db.session.commit()

    client = Client.query.get(job.client_id) if job.client_id else None

    # Get job status
    job_status = JobStatus.query.filter_by(
        job_id=job_id, stage="site_confirmation"
    ).first()
    status = job_status.status if job_status else "incomplete"
    is_completed = status == "complete"

    # Check if edit mode is requested
    edit_mode = request.args.get("edit", "false").lower() == "true"
    # If not completed, always show edit mode
    readonly = is_completed and not edit_mode

    # helper to rebuild the `documents` dict
    def _collect_documents():
        docs = {
            k: []
            for k in (
                "floor_plan",
                "material_quote",
                "signed_agreement",
                "sales_checklist",
                "combined",
                "additional",
            )
        }
        for sdoc in SiteConfirmationDocument.query.filter_by(
            site_confirmation_id=site_confirmation.id
        ).all():
            jdoc = JobDocument.query.get(sdoc.job_document_id)
            if jdoc:
                docs[sdoc.document_type].append(
                    {
                        "id": sdoc.id,
                        "job_document_id": jdoc.id,
                        "filename": jdoc.filename,
                        "uploaded_at": jdoc.uploaded_at,
                        "page_range": sdoc.page_range,
                    }
                )
        return docs

    documents = _collect_documents()

    # ────────────────────────────
    #  Handle POST actions (upload, delete, complete …)
    # ────────────────────────────
    if request.method == "POST" and not readonly:
        try:
            action = request.form.get("action", "")

            # ───── Upload a new document ─────
            if action == "upload_document":
                document_type = request.form.get("document_type")
                file = request.files.get("document_file")
                page_range = request.form.get("page_range", "")

                if not file or not document_type:
                    flash("Missing required fields for document upload.", "error")
                    if edit_mode:
                        return redirect(
                            url_for("site_confirmation", job_id=job_id, edit=True)
                        )
                    return redirect(url_for("site_confirmation", job_id=job_id))

                # secure & unique filename
                original_name = secure_filename(file.filename)
                if not original_name:
                    flash("Invalid file name.", "error")
                    if edit_mode:
                        return redirect(
                            url_for("site_confirmation", job_id=job_id, edit=True)
                        )
                    return redirect(url_for("site_confirmation", job_id=job_id))

                unique_name = f"{datetime.utcnow():%Y%m%d%H%M%S}_{original_name}"
                save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                file.save(save_path)

                # JobDocument
                job_doc = JobDocument(
                    job_id=job_id,
                    user_id=user_id,
                    title=f"Site Confirmation - {document_type.replace('_',' ').title()}",
                    filename=original_name,
                    mime_type=file.content_type,
                    file_path=save_path,
                )
                db.session.add(job_doc)
                db.session.flush()  # so we have job_doc.id

                # SiteConfirmationDocument
                site_doc = SiteConfirmationDocument(
                    site_confirmation_id=site_confirmation.id,
                    job_document_id=job_doc.id,
                    document_type=document_type,
                    page_range=page_range or None,
                )
                db.session.add(site_doc)

                # update flags
                flag_map = {
                    "floor_plan": "has_floor_plan",
                    "material_quote": "has_material_quote",
                    "signed_agreement": "has_signed_agreement",
                    "sales_checklist": "has_sales_checklist",
                    "combined": "has_combined_document",
                }
                if document_type in flag_map:
                    setattr(site_confirmation, flag_map[document_type], True)
                if document_type == "combined":
                    site_confirmation.combined_document_notes = request.form.get(
                        "combined_notes", ""
                    )

                db.session.commit()
                flash("Document uploaded successfully!", "success")

            # ───── Update notes for combined doc ─────
            elif action == "update_combined_notes":
                site_confirmation.combined_document_notes = request.form.get(
                    "combined_notes", ""
                )
                db.session.commit()
                flash("Combined document notes updated.", "success")

            # ───── Save notes without completing ─────
            elif action == "save_notes":
                site_confirmation.notes = request.form.get("notes", "")
                db.session.commit()
                flash("Notes saved successfully.", "success")

            # ───── Mark form complete ─────
            elif action == "complete":
                site_confirmation.notes = request.form.get("notes", "")
                site_confirmation.mark_completed(user_id)
                db.session.commit()
                flash("Site confirmation completed successfully!", "success")
                return redirect(url_for("view_job", job_id=job_id))

            # ───── Delete a document ─────
            elif action == "delete_document":
                doc_id = request.form.get("document_id")
                sdoc = SiteConfirmationDocument.query.get(doc_id) if doc_id else None
                if not sdoc or sdoc.site_confirmation.job_id != job_id:
                    flash("Document not found or access denied.", "error")
                else:
                    jdoc = JobDocument.query.get(sdoc.job_document_id)
                    db.session.delete(sdoc)
                    if jdoc:
                        try:
                            if os.path.exists(jdoc.file_path):
                                os.remove(jdoc.file_path)
                        except Exception as er:
                            logger.error(f"File‑delete error: {er}")
                        db.session.delete(jdoc)

                    # refresh flags
                    for flag, dtype in (
                        ("has_floor_plan", "floor_plan"),
                        ("has_material_quote", "material_quote"),
                        ("has_signed_agreement", "signed_agreement"),
                        ("has_sales_checklist", "sales_checklist"),
                        ("has_combined_document", "combined"),
                    ):
                        remaining = SiteConfirmationDocument.query.filter_by(
                            site_confirmation_id=site_confirmation.id,
                            document_type=dtype,
                        ).count()
                        setattr(site_confirmation, flag, bool(remaining))

                    if not site_confirmation.has_combined_document:
                        site_confirmation.combined_document_notes = None

                    db.session.commit()
                    flash("Document deleted successfully.", "success")

            else:
                flash("Unknown action.", "error")

        except Exception as e:
            db.session.rollback()
            logger.error("Site‑confirmation POST error", exc_info=True)
            flash(f"Error: {e}", "error")

        # refresh document list after any POST
        documents = _collect_documents()

        # Keep edit mode if we were in it
        if edit_mode:
            return redirect(url_for("site_confirmation", job_id=job_id, edit=True))
        return redirect(url_for("site_confirmation", job_id=job_id))

    # ────────────────────────────
    #  Render page
    # ────────────────────────────
    return render_template(
        "site_confirmation.html",
        job=job,
        client=client,
        site_confirmation=site_confirmation,
        documents=documents,
        status=status,
        is_completed=is_completed,
        readonly=readonly,
        edit_mode=edit_mode,
    )


@app.route("/post_installation", defaults={"job_id": None}, methods=["GET", "POST"])
@app.route("/job/<int:job_id>/post_installation", methods=["GET", "POST"])
def post_installation(job_id):
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    user_id = session["user_id"]

    # If no job_id provided, show landing page with job selection
    if job_id is None:
        # Admin sees all jobs; sales see only their own
        if session.get("role") == "admin":
            available_jobs = Job.query.order_by(Job.updated_at.desc()).all()
        else:
            available_jobs = (
                Job.query.filter_by(user_id=user_id)
                .order_by(Job.updated_at.desc())
                .all()
            )

        return render_template("post_installation_landing.html", jobs=available_jobs)

    # If job_id provided, check permissions and get the job
    job = Job.query.get_or_404(job_id)
    if job.user_id != user_id and session.get("role") != "admin":
        flash("You don't have permission to access this job.", "error")
        return redirect(url_for("dashboard"))

    # Get client information
    client = Client.query.get(job.client_id) if job.client_id else None

    # Get or create post-installation record
    post_install = PostInstallation.query.filter_by(job_id=job_id).first()
    if not post_install:
        post_install = PostInstallation(job_id=job_id)
        db.session.add(post_install)
        db.session.commit()

    # Get job status
    job_status = JobStatus.query.filter_by(
        job_id=job_id, stage="post_installation"
    ).first()
    status = job_status.status if job_status else "incomplete"
    is_completed = status == "complete"

    # Check if edit mode is requested
    edit_mode = request.args.get("edit", "false").lower() == "true"
    # If not completed, always show edit mode
    readonly = is_completed and not edit_mode

    # Helper to gather documents
    def collect_documents():
        docs = {"permit_plan": [], "final_invoice": [], "additional": []}
        for doc in PostInstallationDocument.query.filter_by(
            post_installation_id=post_install.id
        ).all():
            job_doc = JobDocument.query.get(doc.job_document_id)
            if job_doc:
                docs[doc.document_type].append(
                    {
                        "id": doc.id,
                        "job_document_id": job_doc.id,
                        "filename": job_doc.filename,
                        "uploaded_at": job_doc.uploaded_at,
                    }
                )
        return docs

    documents = collect_documents()

    # Process POST requests
    if request.method == "POST":
        action = request.form.get("action", "")

        try:
            # Handle document upload
            if action == "upload_document":
                document_type = request.form.get("document_type")
                file = request.files.get("document_file")

                if not file or not document_type:
                    flash("Missing required fields for document upload.", "error")
                    return redirect(url_for("post_installation", job_id=job_id))

                # Secure and save the file
                original_name = secure_filename(file.filename)
                if not original_name:
                    flash("Invalid file name.", "error")
                    return redirect(url_for("post_installation", job_id=job_id))

                unique_name = f"{datetime.utcnow():%Y%m%d%H%M%S}_{original_name}"
                save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                file.save(save_path)

                # Create job document
                job_doc = JobDocument(
                    job_id=job_id,
                    user_id=user_id,
                    title=f"Post-Installation - {document_type.replace('_', ' ').title()}",
                    filename=original_name,
                    mime_type=file.content_type,
                    file_path=save_path,
                )
                db.session.add(job_doc)
                db.session.flush()

                # Create post-installation document
                post_doc = PostInstallationDocument(
                    post_installation_id=post_install.id,
                    job_document_id=job_doc.id,
                    document_type=document_type,
                )
                db.session.add(post_doc)
                db.session.commit()

                flash("Document uploaded successfully!", "success")

            # Handle save notes
            elif action == "save_notes":
                post_install.notes = request.form.get("notes", "")
                db.session.commit()
                flash("Notes saved successfully!", "success")

            # Handle complete
            elif action == "complete":
                # Save form data first
                finish_items = request.form.getlist("finish_items[]")
                labor_items = request.form.getlist("labor_items[]")
                parts_items = request.form.getlist("parts_items[]")
                permit_items = request.form.getlist("permit_items[]")

                # Filter out empty items
                post_install.finish_items = [
                    item for item in finish_items if item.strip()
                ]
                post_install.labor_items = [
                    item for item in labor_items if item.strip()
                ]
                post_install.parts_items = [
                    item for item in parts_items if item.strip()
                ]
                post_install.permit_items = [
                    item for item in permit_items if item.strip()
                ]

                # Save notes if provided
                if request.form.get("notes"):
                    post_install.notes = request.form.get("notes")

                # Mark as completed
                post_install.mark_completed(user_id)
                db.session.commit()

                flash("Post-installation completed successfully!", "success")
                return redirect(url_for("view_job", job_id=job_id))

            # Handle save without completing
            elif action == "save":
                # Save form data
                finish_items = request.form.getlist("finish_items[]")
                labor_items = request.form.getlist("labor_items[]")
                parts_items = request.form.getlist("parts_items[]")
                permit_items = request.form.getlist("permit_items[]")

                # Filter out empty items
                post_install.finish_items = [
                    item for item in finish_items if item.strip()
                ]
                post_install.labor_items = [
                    item for item in labor_items if item.strip()
                ]
                post_install.parts_items = [
                    item for item in parts_items if item.strip()
                ]
                post_install.permit_items = [
                    item for item in permit_items if item.strip()
                ]

                # Save notes if provided
                if request.form.get("notes"):
                    post_install.notes = request.form.get("notes")

                db.session.commit()
                flash("Changes saved successfully!", "success")

                # Stay in edit mode if we were in edit mode
                if edit_mode:
                    return redirect(
                        url_for("post_installation", job_id=job_id, edit=True)
                    )
                return redirect(url_for("post_installation", job_id=job_id))

            # Refresh documents after any action
            documents = collect_documents()

        except Exception as e:
            db.session.rollback()
            logger.error(f"Post-installation error: {str(e)}")
            flash(f"Error: {str(e)}", "error")

    # Render the template with all data
    return render_template(
        "post_installation.html",
        job=job,
        client=client,
        post_install=post_install,
        documents=documents,
        status=status,
        is_completed=is_completed,
        readonly=readonly,
        edit_mode=edit_mode,
    )


# -----------------------------------------------------------
#  Main sheet – /job/<id>/pre_installation   (GET & POST)
# -----------------------------------------------------------
@app.route("/job/<int:job_id>/pre_installation", methods=["GET", "POST"])
def pre_installation(job_id):
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    user_id = session["user_id"]
    job = Job.query.get_or_404(job_id)

    if job.user_id != user_id and session.get("role") != "admin":
        flash("You don't have permission to access this job.", "error")
        return redirect(url_for("dashboard"))

    # Get or create pre-installation record
    pi = PreInstallation.query.filter_by(job_id=job_id).first()
    if not pi:
        pi = PreInstallation(job_id=job_id)
        db.session.add(pi)
        db.session.commit()

    # Get job status
    job_status = JobStatus.query.filter_by(
        job_id=job_id, stage="pre_installation"
    ).first()
    status = job_status.status if job_status else "incomplete"
    is_completed = status == "complete"

    # Check if edit mode is requested
    edit_mode = request.args.get("edit", "false").lower() == "true"
    # If not completed, always show edit mode
    readonly = is_completed and not edit_mode

    # Helper – gather documents by type for the template
    def _collect_docs():
        buckets = {
            k: []
            for k in [
                "custom_door_design",
                "mod_dap",
                "approved_proposal",
                "final_material_order",
                "final_permit_floor_plan",
                "job_site_photos",
                "additional",
            ]
        }
        for doc in PreInstallationDocument.query.filter_by(
            pre_installation_id=pi.id
        ).all():
            jd = JobDocument.query.get(doc.job_document_id)
            if jd:
                buckets[doc.document_type].append(
                    {
                        "id": doc.id,
                        "job_document_id": jd.id,
                        "filename": jd.filename,
                        "uploaded_at": jd.uploaded_at,
                        "page_range": doc.page_range,
                    }
                )
        return buckets

    documents = _collect_docs()

    # ------------------------------------------------------------------
    # POST processing (upload, update, delete, complete)
    # ------------------------------------------------------------------
    if request.method == "POST" and not readonly:
        action = request.form.get("action", "")

        try:
            # ------------------ 1) file upload -------------------------
            if action == "upload_document":
                document_type = request.form.get("document_type")
                file = request.files.get("document_file")
                page_range = request.form.get("page_range", "")

                if not file or not document_type:
                    flash("Missing file or type.", "error")
                    return redirect(request.url)

                original = secure_filename(file.filename)
                if original == "":
                    flash("Invalid filename.", "error")
                    return redirect(request.url)

                unique = f"{datetime.utcnow():%Y%m%d%H%M%S}_{original}"
                path = os.path.join(app.config["UPLOAD_FOLDER"], unique)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                file.save(path)

                jd = JobDocument(
                    job_id=job_id,
                    user_id=user_id,
                    title=f"Pre‑Install – {document_type.replace('_',' ').title()}",
                    filename=original,
                    mime_type=file.content_type,
                    file_path=path,
                )
                db.session.add(jd)
                db.session.flush()

                pid = PreInstallationDocument(
                    pre_installation_id=pi.id,
                    job_document_id=jd.id,
                    document_type=document_type,
                    page_range=page_range or None,
                )
                db.session.add(pid)

                # toggle flags
                if document_type == "custom_door_design":
                    pi.has_custom_door_design = True
                elif document_type == "mod_dap":
                    pi.has_mod_dap = True
                elif document_type == "approved_proposal":
                    pi.has_approved_proposal = True
                elif document_type == "final_material_order":
                    pi.has_final_material_order = True
                elif document_type == "final_permit_floor_plan":
                    pi.has_final_permit_floor_plan = True
                elif document_type == "job_site_photos":
                    pi.has_job_site_photos = True

                db.session.commit()
                flash("Document uploaded.", "success")

                # Preserve edit mode if we were in it
                if edit_mode:
                    return redirect(
                        url_for("pre_installation", job_id=job_id, edit=True)
                    )
                return redirect(request.url)

            # ------------------ 2) save / continue (notes + options) ----
            elif action == "save":
                pi.notes = request.form.get("notes", "")
                pi.door_option = request.form.get("door_option")

                pi.casing_standard = bool(request.form.get("casing_standard"))
                pi.casing_special = bool(request.form.get("casing_special"))

                # cleaning
                pi.cleaning_dust = bool(request.form.get("cleaning_dust"))
                pi.cleaning_vacuum = bool(request.form.get("cleaning_vacuum"))
                pi.cleaning_mop = bool(request.form.get("cleaning_mop"))
                pi.cleaning_change_beds = bool(request.form.get("cleaning_change_beds"))
                pi.cleaning_windows = bool(request.form.get("cleaning_windows"))
                pi.cleaning_none = bool(request.form.get("cleaning_none"))

                db.session.commit()
                flash("Saved.", "success")

                # Preserve edit mode if we were in it
                if edit_mode:
                    return redirect(
                        url_for("pre_installation", job_id=job_id, edit=True)
                    )
                return redirect(request.url)

            # ------------------ 3) mark complete ------------------------
            elif action == "complete":
                # First save all the form data
                pi.notes = request.form.get("notes", "")
                pi.door_option = request.form.get("door_option")
                pi.casing_standard = bool(request.form.get("casing_standard"))
                pi.casing_special = bool(request.form.get("casing_special"))
                pi.cleaning_dust = bool(request.form.get("cleaning_dust"))
                pi.cleaning_vacuum = bool(request.form.get("cleaning_vacuum"))
                pi.cleaning_mop = bool(request.form.get("cleaning_mop"))
                pi.cleaning_change_beds = bool(request.form.get("cleaning_change_beds"))
                pi.cleaning_windows = bool(request.form.get("cleaning_windows"))
                pi.cleaning_none = bool(request.form.get("cleaning_none"))

                # Then mark as completed
                pi.mark_completed(user_id)
                db.session.commit()
                flash("Pre‑installation completed!", "success")
                return redirect(url_for("view_job", job_id=job_id))

            # ------------------ 4) delete document ----------------------
            elif action == "delete_document":
                doc_id = request.form.get("document_id")
                doc = PreInstallationDocument.query.get(doc_id) if doc_id else None
                if not doc or doc.pre_installation_id != pi.id:
                    flash("Doc not found.", "error")
                else:
                    jd = JobDocument.query.get(doc.job_document_id)
                    if jd and os.path.exists(jd.file_path):
                        os.remove(jd.file_path)
                    if jd:
                        db.session.delete(jd)
                    db.session.delete(doc)
                    db.session.commit()
                    flash("Document deleted.", "success")

                # Preserve edit mode if we were in it
                if edit_mode:
                    return redirect(
                        url_for("pre_installation", job_id=job_id, edit=True)
                    )
                return redirect(request.url)

        except Exception as ex:
            db.session.rollback()
            logger.error(f"Pre‑Install error: {ex}")
            flash(str(ex), "error")

            # Preserve edit mode if we were in it
            if edit_mode:
                return redirect(url_for("pre_installation", job_id=job_id, edit=True))
            return redirect(request.url)

        # refresh doc list after any POST
        documents = _collect_docs()

    # ------------------------------------------------------------------
    # GET – render the sheet
    # ------------------------------------------------------------------
    client = Client.query.get(job.client_id) if job.client_id else None

    return render_template(
        "pre_installation.html",
        job=job,
        client=client,
        pre_install=pi,
        documents=documents,
        status=status,
        is_completed=is_completed,
        readonly=readonly,
        edit_mode=edit_mode,
    )


# -----------------------------------------------------------
# Run the Application
# -----------------------------------------------------------
if __name__ == "__main__":
    # Create tables if they don't exist already
    db.create_all()
    app.run(debug=True)
