"""offline recovery codes: only their hashes (L14)

Revision ID: 0027_recovery_codes
Revises: 0026_activity_notifications
Create Date: 2026-09-24

Diez códigos de un solo uso por cuenta, para recuperar el acceso sin email.
Cada código tiene 150 bits aleatorios, así que basta el SHA-256 de
`hash_token()` (mismo criterio que las sesiones): no hay diccionario que
precomputar y no hace falta un secreto nuevo que custodiar. El texto en claro
solo existe en la respuesta que los genera.
"""
from alembic import op


revision = "0027_recovery_codes"
down_revision = "0026_activity_notifications"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE recovery_codes (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            code_hash CHAR(64) NOT NULL UNIQUE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            used_at TIMESTAMP NULL,
            CONSTRAINT chk_recovery_codes_hash CHECK (code_hash ~ '^[0-9a-f]{64}$')
        )
        """
    )
    op.execute("CREATE INDEX idx_recovery_codes_user ON recovery_codes (user_id) WHERE used_at IS NULL")


def downgrade():
    # Borrar la tabla con códigos dentro dejaría a esas cuentas sin su vía de
    # recuperación sin avisarles. Se para en seco.
    guardados = op.get_bind().exec_driver_sql("SELECT COUNT(*) FROM recovery_codes").scalar()
    if guardados:
        raise RuntimeError(
            f"No se puede bajar de 0027: hay {guardados} código(s) en recovery_codes. "
            "Revócalos antes de bajar para no dejar cuentas sin recuperación en silencio."
        )
    op.execute("DROP TABLE recovery_codes")
