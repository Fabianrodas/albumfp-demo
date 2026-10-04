"""passkeys (WebAuthn): public credentials + one-time challenges (L15)

Revision ID: 0028_webauthn_passkeys
Revises: 0027_recovery_codes
Create Date: 2026-09-24

Una credencial guarda solo datos PÚBLICOS: el id que eligió el autenticador,
su clave pública COSE y el contador. La clave privada nunca sale del
dispositivo, así que un volcado de esta tabla no deja entrar a nadie.

Los challenges se guardan como SHA-256 (igual que sesiones y códigos), viven
minutos, se consumen con un DELETE ... RETURNING y los de registro quedan
atados a la cuenta Y a la sesión que los pidió.
"""
from alembic import op


revision = "0028_webauthn_passkeys"
down_revision = "0027_recovery_codes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE webauthn_credentials (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            credential_id BYTEA NOT NULL UNIQUE,
            public_key BYTEA NOT NULL,
            sign_count BIGINT NOT NULL DEFAULT 0,
            transports TEXT[] NOT NULL DEFAULT '{}',
            nickname VARCHAR(60) NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_used_at TIMESTAMP NULL,
            CONSTRAINT chk_webauthn_sign_count CHECK (sign_count >= 0),
            CONSTRAINT chk_webauthn_nickname CHECK (length(btrim(nickname)) > 0)
        )
        """
    )
    op.execute("CREATE INDEX idx_webauthn_credentials_user ON webauthn_credentials (user_id)")
    op.execute(
        """
        CREATE TABLE webauthn_challenges (
            id BIGSERIAL PRIMARY KEY,
            challenge_hash CHAR(64) NOT NULL UNIQUE,
            purpose VARCHAR(20) NOT NULL,
            user_id INTEGER NULL REFERENCES users(id) ON DELETE CASCADE,
            session_id BIGINT NULL REFERENCES user_sessions(id) ON DELETE CASCADE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            CONSTRAINT chk_webauthn_challenge_hash CHECK (challenge_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT chk_webauthn_challenge_purpose CHECK (purpose IN ('registration', 'authentication')),
            CONSTRAINT chk_webauthn_challenge_binding CHECK (
                (purpose = 'registration') = (user_id IS NOT NULL AND session_id IS NOT NULL))
        )
        """
    )
    op.execute("CREATE INDEX idx_webauthn_challenges_expires ON webauthn_challenges (expires_at)")


def downgrade():
    # Bajar con passkeys dentro las borraría sin avisar a sus dueños. Los
    # challenges son efímeros: esos sí se pueden tirar.
    guardadas = op.get_bind().exec_driver_sql("SELECT COUNT(*) FROM webauthn_credentials").scalar()
    if guardadas:
        raise RuntimeError(
            f"No se puede bajar de 0028: hay {guardadas} passkey(s) en webauthn_credentials. "
            "Revócalas antes de bajar para no dejar cuentas sin su forma de entrar en silencio."
        )
    op.execute("DROP TABLE webauthn_challenges")
    op.execute("DROP TABLE webauthn_credentials")
