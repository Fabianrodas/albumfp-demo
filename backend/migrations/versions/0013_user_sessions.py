"""sesiones opacas del servidor en vez de JWT en el navegador

Revision ID: 0013_user_sessions
Revises: 0012_private_tags
Create Date: 2026-08-28

Se guarda **solo el SHA-256** del token de sesion y del secreto CSRF, nunca su
valor: un dump de esta base no permite entrar a nadie. La columna es CHAR(64)
porque un hex de SHA-256 mide exactamente eso.

El plan pedia `TIMESTAMPTZ`. Se usa `TIMESTAMP` sin zona como TODO el resto
del esquema: una columna con zona reintroduciria el desplazamiento horario que
arreglo la fase 02 (`NaiveDatetimeJSONProvider`), y la propiedad de seguridad
no cambia -- la caducidad la compara Postgres contra su propio `NOW()`, no el
navegador.

El indice es **parcial** (`WHERE revoked_at IS NULL`): las sesiones cerradas se
conservan como registro pero ninguna consulta las busca, asi que no ocupan
sitio en el indice. Mismo criterio que el indice de coordenadas de la fase 02.

No hay backfill: las sesiones JWT que hubiera vivas no se pueden convertir en
filas (el servidor nunca las guardo). Todo el mundo tendra que volver a
iniciar sesion una vez, y eso es lo correcto -- es justamente el punto de
poder revocar.
"""
from alembic import op

revision = "0013_user_sessions"
down_revision = "0012_private_tags"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE user_sessions (
            id BIGSERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            token_hash CHAR(64) NOT NULL UNIQUE,
            csrf_hash CHAR(64) NOT NULL,
            remember_me BOOLEAN NOT NULL DEFAULT FALSE,
            user_agent_summary VARCHAR(240) NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_used_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            revoked_at TIMESTAMP NULL,

            CONSTRAINT fk_session_user FOREIGN KEY (user_id)
                REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_user_sessions_user_active
        ON user_sessions(user_id, expires_at)
        WHERE revoked_at IS NULL
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS user_sessions")
