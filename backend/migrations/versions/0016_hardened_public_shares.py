"""enlaces publicos endurecidos

Revision ID: 0016_hardened_public_shares
Revises: 0015_ip_reputation_cache
Create Date: 2026-08-29

`album_shares.token` (compartido por enlaces publicos e invitaciones de
cuenta pendientes) se guardaba en texto plano. Un dump de la base lo habria
dejado usable de inmediato para entrar a cualquier album compartido --
exactamente el problema que S01 ya resolvio para las sesiones. Se aplica el
mismo patron: se guarda solo sha256(token) en `token_hash`, el crudo solo
existe en la respuesta HTTP que lo crea (`hash_token()` de
`app/security/sessions.py`, reutilizado tal cual). Los enlaces ya emitidos
siguen funcionando: el valor del token en la URL no cambia, solo como se
guarda en el servidor.

Un enlace publico gana tres controles nuevos, todos con el default mas
restrictivo salvo mostrar metadatos (que hoy no expone nada sensible -- ver
CLAUDE.md): `password_hash` (Argon2, opcional), `allow_original_download`
(apagado por defecto: antes cualquier enlace de solo lectura podia bajar el
archivo original completo sin que el dueno lo decidiera), `show_metadata`
(encendido, para cuando exista una vista que muestre metadatos).

`share_unlocks` es una sesion anonima minima para el visitante de un enlace
con contrasena: se prueba la contrasena una vez contra `password_hash`, y una
cookie (nunca la contrasena en la URL) autoriza las peticiones siguientes.
Mismo patron que `user_sessions`/`registration_invites`: solo se guarda
sha256(token) de la cookie, nunca el valor real.
"""
import hashlib

import sqlalchemy as sa
from alembic import op

revision = "0016_hardened_public_shares"
down_revision = "0015_ip_reputation_cache"
branch_labels = None
depends_on = None


def _hash(raw: str) -> str:
    """Debe coincidir exactamente con `app.security.sessions.hash_token`."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def upgrade():
    op.execute("ALTER TABLE album_shares ADD COLUMN token_hash CHAR(64)")
    op.execute("ALTER TABLE album_shares ADD COLUMN password_hash TEXT")
    op.execute("ALTER TABLE album_shares ADD COLUMN allow_original_download BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE album_shares ADD COLUMN show_metadata BOOLEAN NOT NULL DEFAULT TRUE")

    conexion = op.get_bind()
    filas = conexion.execute(sa.text("SELECT id, token FROM album_shares WHERE token IS NOT NULL")).fetchall()
    for fila in filas:
        conexion.execute(
            sa.text("UPDATE album_shares SET token_hash = :hash WHERE id = :id"),
            {"hash": _hash(fila.token), "id": fila.id},
        )

    op.execute("DROP INDEX idx_album_shares_token")
    op.execute("ALTER TABLE album_shares DROP COLUMN token")
    op.execute("ALTER TABLE album_shares ADD CONSTRAINT uq_album_shares_token_hash UNIQUE (token_hash)")
    op.execute(
        """
        ALTER TABLE album_shares ADD CONSTRAINT chk_public_link_only_extras CHECK (
            share_type = 'public_link'
            OR (password_hash IS NULL AND allow_original_download = FALSE AND show_metadata = TRUE)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE share_unlocks (
            id SERIAL PRIMARY KEY,
            share_id INTEGER NOT NULL,
            token_hash CHAR(64) NOT NULL UNIQUE,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            CONSTRAINT fk_share_unlock_share FOREIGN KEY (share_id)
                REFERENCES album_shares(id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX idx_share_unlocks_share ON share_unlocks(share_id)")
    op.execute("CREATE INDEX idx_share_unlocks_expires ON share_unlocks(expires_at)")


def downgrade():
    # El token crudo no se puede recuperar desde su hash: los enlaces
    # publicos ya emitidos quedarian inservibles tras bajar esta revision
    # (habria que reemitirlos). Aceptado a proposito, igual que S01 nunca
    # ofrecio downgrade para volver a JWT.
    op.execute("DROP TABLE share_unlocks")
    op.execute("ALTER TABLE album_shares DROP CONSTRAINT chk_public_link_only_extras")
    op.execute("ALTER TABLE album_shares DROP CONSTRAINT uq_album_shares_token_hash")
    op.execute("ALTER TABLE album_shares ADD COLUMN token TEXT")
    op.execute("CREATE UNIQUE INDEX idx_album_shares_token ON album_shares(token)")
    op.execute("ALTER TABLE album_shares DROP COLUMN show_metadata")
    op.execute("ALTER TABLE album_shares DROP COLUMN allow_original_download")
    op.execute("ALTER TABLE album_shares DROP COLUMN password_hash")
    op.execute("ALTER TABLE album_shares DROP COLUMN token_hash")
