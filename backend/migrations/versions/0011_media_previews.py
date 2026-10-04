"""vista previa WebP local de cada foto

Revision ID: 0011_media_previews
Revises: 0010_notification_preferences
Create Date: 2026-08-27

Las columnas van en `media_metadata` y no en `media` a proposito: `media` se
lee entero en cada listado, y la vista previa solo la necesitan la entrega del
archivo y el borrado. `media_metadata` ya es 1:1 y ya se consulta con LEFT
JOIN justo donde hace falta.

Nullable las cuatro: una foto subida antes de esta fase, un video, o una
imagen cuya vista previa fallo se quedan sin ellas y la entrega cae al
original. No hay backfill automatico -- `scripts/backfill_previews.py` es
explicito, como pide el plan.
"""
from alembic import op

revision = "0011_media_previews"
down_revision = "0010_notification_preferences"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE media_metadata
            ADD COLUMN preview_storage_path TEXT NULL,
            ADD COLUMN preview_mime_type VARCHAR(100) NULL,
            ADD COLUMN preview_width INTEGER NULL,
            ADD COLUMN preview_height INTEGER NULL
        """
    )


def downgrade():
    op.execute(
        """
        ALTER TABLE media_metadata
            DROP COLUMN IF EXISTS preview_storage_path,
            DROP COLUMN IF EXISTS preview_mime_type,
            DROP COLUMN IF EXISTS preview_width,
            DROP COLUMN IF EXISTS preview_height
        """
    )
