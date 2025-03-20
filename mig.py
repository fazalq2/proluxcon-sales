# In a migration script or in your Flask shell
from flask_migrate import Migrate
from app import app, db

# Ensure you have Migrate initialized
migrate = Migrate(app, db)


# Create a migration to add the job_id column
def create_job_id_migration():
    from alembic import op
    import sqlalchemy as sa

    # Alter the report table to add job_id column
    op.add_column("report", sa.Column("job_id", sa.Integer(), nullable=True))

    # Optionally, add a foreign key constraint
    op.create_foreign_key("fk_report_job", "report", "job", ["job_id"], ["id"])
