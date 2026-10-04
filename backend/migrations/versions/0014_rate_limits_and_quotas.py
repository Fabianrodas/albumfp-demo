"""limites de abuso, invitaciones de registro y tamano de vista previa

Revision ID: 0014_rate_limits_and_quotas
Revises: 0013_user_sessions
Create Date: 2026-08-29

Tres cosas nuevas, sin relacion entre si salvo pertenecer a la misma fase:

- `rate_limit_counters`: contadores de ventana fija para login/registro
  (`app/security/rate_limit.py`). Clave (scope, identifier, window_key) en vez
  de un solo INTEGER autoincremental porque el UPDATE atomico de
  `try_consume` necesita poder apuntar a la fila exacta sin una consulta
  previa. Indice sobre `window_ends_at` para que la purga (borrar lo vencido)
  no recorra la tabla entera.

- `registration_invites`: igual que `user_sessions`, se guarda solo el
  SHA-256 del token, nunca el token. Sin FK de "quien lo creo": los invita
  siempre el operador del servidor via el endpoint interno
  (`X-Internal-Token`, mismo que la purga de papelera), no otro usuario, asi
  que no hace falta rastrear una cuenta creadora.

- `media_metadata.preview_file_size`: la vista previa WebP (fase 13) nunca
  guardo su propio tamano -- `create_image_preview` ya lo calculaba
  (`temporal.stat().st_size`, para decidir si descartarla) y lo tiraba. Hace
  falta persistido para calcular el uso de almacenamiento de una cuenta sin
  tener que hacer `stat()` de cada archivo en cada comprobacion de cuota.
"""
from alembic import op

revision = "0014_rate_limits_and_quotas"
down_revision = "0013_user_sessions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE rate_limit_counters (
            scope VARCHAR(40) NOT NULL,
            identifier VARCHAR(255) NOT NULL,
            window_key VARCHAR(20) NOT NULL,
            request_count INTEGER NOT NULL DEFAULT 0,
            window_ends_at TIMESTAMP NOT NULL,

            PRIMARY KEY (scope, identifier, window_key)
        )
        """
    )
    op.execute("CREATE INDEX idx_rate_limit_window_ends ON rate_limit_counters(window_ends_at)")

    op.execute(
        """
        CREATE TABLE registration_invites (
            id BIGSERIAL PRIMARY KEY,
            token_hash CHAR(64) NOT NULL UNIQUE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            used_at TIMESTAMP NULL,
            used_by_user_id INTEGER NULL,

            CONSTRAINT fk_invite_used_by FOREIGN KEY (used_by_user_id)
                REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )

    op.execute("ALTER TABLE media_metadata ADD COLUMN preview_file_size INTEGER NULL")


def downgrade():
    op.execute("ALTER TABLE media_metadata DROP COLUMN IF EXISTS preview_file_size")
    op.execute("DROP TABLE IF EXISTS registration_invites")
    op.execute("DROP TABLE IF EXISTS rate_limit_counters")
