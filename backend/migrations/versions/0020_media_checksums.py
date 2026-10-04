"""persist media SHA-256 checksums for exact duplicate detection

Revision ID: 0020_media_checksums
Revises: 0019_indexed_search
Create Date: 2026-09-18

Nullable is deliberate: existing objects are backfilled by an explicit,
resumable CLI because a database migration cannot safely read local/remote
object storage. The index is deliberately not unique: an owner may confirm an
exact duplicate with force_duplicate=true.
"""
from alembic import op


revision = "0020_media_checksums"
down_revision = "0019_indexed_search"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE media_metadata
            ADD COLUMN sha256 CHAR(64) NULL,
            ADD CONSTRAINT chk_media_metadata_sha256
                CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$')
        """
    )
    op.execute(
        """
        CREATE INDEX idx_media_metadata_sha256
        ON media_metadata (sha256)
        WHERE sha256 IS NOT NULL
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_media_metadata_sha256")
    op.execute(
        "ALTER TABLE media_metadata "
        "DROP CONSTRAINT IF EXISTS chk_media_metadata_sha256"
    )
    op.execute("ALTER TABLE media_metadata DROP COLUMN IF EXISTS sha256")
