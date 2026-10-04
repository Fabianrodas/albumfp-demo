"""nivel de permiso 'upload' para colaboradores

Revision ID: 0004_share_permission_levels
Revises: 0003_location_context
Create Date: 2026-08-22

Hasta ahora un colaborador solo podia ser 'read' o 'write', y 'write' daba
acceso a TODO lo que no fuera exclusivo del dueno (subir/borrar media, editar
fotos, favoritos, tags, editar el album). El dueno pidio poder invitar a
alguien que solo suba/edite sus propias fotos sin tocar favoritos, tags ni el
album: 'upload' se inserta entre 'read' y 'write' en la jerarquia
(read < upload < write < owner). 'owner' nunca se otorga por share, sigue
siendo exclusivo de quien creo el album.

Compartir/revocar acceso, la papelera del album y desactivarlo siguen
exigiendo 'owner' real: ningun nivel de colaborador los alcanza, con o sin
este cambio.
"""
from alembic import op

revision = "0004_share_permission_levels"
down_revision = "0003_location_context"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE album_shares DROP CONSTRAINT chk_permission")
    op.execute("ALTER TABLE album_shares ADD CONSTRAINT chk_permission CHECK (permission IN ('read', 'upload', 'write'))")


def downgrade():
    op.execute("ALTER TABLE album_shares DROP CONSTRAINT chk_permission")
    op.execute("ALTER TABLE album_shares ADD CONSTRAINT chk_permission CHECK (permission IN ('read', 'write'))")
