"""index the owner's stable chronological library timeline

Revision ID: 0021_library_timeline
Revises: 0020_media_checksums
Create Date: 2026-09-18
"""
from alembic import op


revision = "0021_library_timeline"
down_revision = "0020_media_checksums"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE INDEX idx_media_library_timeline
        ON media (user_id, (COALESCE(taken_at, created_at)) DESC, id DESC)
        WHERE deleted_at IS NULL
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_media_library_timeline")
