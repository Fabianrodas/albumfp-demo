"""registrar el ultimo acceso a un enlace publico

Revision ID: 0017_share_access_tracking
Revises: 0016_hardened_public_shares
Create Date: 2026-08-30

El dueno de un album puede terminar con varios enlaces publicos sin
etiqueta que los distinga entre si (S08 no guarda el token crudo, asi que
tampoco puede volver a mostrar la URL). Para poder distinguirlos igual, cada
enlace guarda cuando se abrio por ultima vez y un resumen del dispositivo
que lo abrio -- mismo dato y mismo recorte que `user_sessions.
user_agent_summary` (`summarize_user_agent()` de `app/security/sessions.py`,
reutilizado tal cual). A proposito NO se guarda la IP ni una ubicacion
geografica: ningun otro rincon de esta app lo hace (ni siquiera las propias
sesiones de cuenta), asi que un enlace publico no iba a ser la excepcion.
"""
from alembic import op

revision = "0017_share_access_tracking"
down_revision = "0016_hardened_public_shares"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE album_shares ADD COLUMN last_accessed_at TIMESTAMP NULL")
    op.execute("ALTER TABLE album_shares ADD COLUMN last_accessed_user_agent VARCHAR(240) NULL")


def downgrade():
    op.execute("ALTER TABLE album_shares DROP COLUMN last_accessed_user_agent")
    op.execute("ALTER TABLE album_shares DROP COLUMN last_accessed_at")
