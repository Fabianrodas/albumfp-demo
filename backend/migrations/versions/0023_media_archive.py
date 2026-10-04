"""archive media without trashing it

Revision ID: 0023_media_archive
Revises: 0022_on_this_day_home
Create Date: 2026-09-19
"""
from alembic import op


revision = "0023_media_archive"
down_revision = "0022_on_this_day_home"
branch_labels = None
depends_on = None


def upgrade():
    # Sin zona, como deleted_at y el resto del esquema.
    op.execute("ALTER TABLE media ADD COLUMN archived_at TIMESTAMP NULL")
    # La pagina Archivo recorre solo lo archivado, no la cronologia entera.
    op.execute(
        """
        CREATE INDEX idx_media_archive
        ON media (user_id, (COALESCE(taken_at, created_at)) DESC, id DESC)
        WHERE deleted_at IS NULL AND archived_at IS NOT NULL
        """
    )


def downgrade():
    # Borrar la columna devolveria cada recuerdo archivado al flujo diario sin
    # avisar a nadie. Se para en seco: hay que desarchivar antes de bajar.
    archivados = op.get_bind().exec_driver_sql(
        "SELECT COUNT(*) FROM media WHERE archived_at IS NOT NULL"
    ).scalar()
    if archivados:
        raise RuntimeError(
            f"No se puede bajar de 0023: hay {archivados} recuerdo(s) archivado(s). "
            "Desarchivalos antes de bajar para no perder ese estado en silencio."
        )
    op.execute("DROP INDEX IF EXISTS idx_media_archive")
    op.execute("ALTER TABLE media DROP COLUMN IF EXISTS archived_at")
