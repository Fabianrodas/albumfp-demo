"""copia cifrada y reversible del token de un enlace

Revision ID: 0018_reversible_share_tokens
Revises: 0017_share_access_tracking
Create Date: 2026-08-31

Decision consultada con el usuario: S08 dejo `token_hash` (SHA-256, de una
sola via) como UNICO rastro del token en la base -- correcto para resolver
un enlace entrante, pero eso tambien significa que el enlace crudo no se
puede volver a mostrar nunca, ni para el propio dueno. La alternativa que el
usuario prefirio es guardar TAMBIEN una copia cifrada y reversible
(Fernet, `app/security/share_token_crypto.py`), con la clave de cifrado
fuera de la base (`SHARE_TOKEN_ENCRYPTION_KEY`) -- un volcado SOLO de la
base sigue sin servir para nada, igual que antes; hace falta ademas la
clave del servidor.

Sin backfill posible ni necesario: las filas de ANTES de esta migracion ya
perdieron su token en claro (solo quedo el hash), asi que no hay nada que
cifrar retroactivamente. Esas filas seguiran funcionando igual para
resolver el enlace; "ver de nuevo" simplemente no estara disponible hasta
que el dueno las regenere una vez con la clave ya configurada.
"""
from alembic import op

revision = "0018_reversible_share_tokens"
down_revision = "0017_share_access_tracking"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE album_shares ADD COLUMN token_encrypted TEXT NULL")


def downgrade():
    op.execute("ALTER TABLE album_shares DROP COLUMN token_encrypted")
