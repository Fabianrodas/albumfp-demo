"""index owner-scoped calendar memories for the personal home

Revision ID: 0022_on_this_day_home
Revises: 0021_library_timeline
Create Date: 2026-09-19
"""
from alembic import op


revision = "0022_on_this_day_home"
down_revision = "0021_library_timeline"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE INDEX idx_media_on_this_day
        ON media (
            user_id,
            (EXTRACT(MONTH FROM taken_at)),
            (EXTRACT(DAY FROM taken_at)),
            (EXTRACT(YEAR FROM taken_at)) DESC,
            taken_at DESC,
            id DESC
        )
        WHERE deleted_at IS NULL AND taken_at IS NOT NULL
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_media_on_this_day")
