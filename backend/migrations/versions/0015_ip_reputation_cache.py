"""cache local de reputacion de IP (AbuseIPDB)

Revision ID: 0015_ip_reputation_cache
Revises: 0014_rate_limits_and_quotas
Create Date: 2026-08-29

`security_ip_reputation` la llena SOLO un job programado
(`python -m app.cli refresh-ip-reputation`, ver `app/integrations/abuseipdb.py`
y `app/security/ip_reputation.py`) -- nunca una peticion HTTP. La cuota
gratuita del endpoint de blacklist de AbuseIPDB es de apenas 5
peticiones/dia (comprobado en su documentacion oficial), muy por debajo de lo
que soportaria quedar detras de un endpoint publico como los demas jobs de
`app/api/jobs.py`.

TIMESTAMP sin zona, como el resto del esquema (ver CLAUDE.md, nota sobre el
amanecer/atardecer: el plan de esa fase tambien pedia TIMESTAMPTZ y se
descarto por el mismo motivo) -- no hace falta la unica columna con zona de
toda la base para un dato que solo lee el propio backend.

`network_or_ip` es PK: cada refresh exitoso reemplaza la tabla entera dentro
de una sola transaccion (DELETE + INSERT masivo), asi que no hace falta un id
autoincremental ni actualizar fila por fila. `total_reports` y `country_code`
quedan NULL con este proveedor: el endpoint de blacklist gratuito solo trae
`ipAddress` y `abuseConfidenceScore` por fila, ningun otro campo -- mismo
patron que `media_context.timezone` o `detected_language` con otros
proveedores (columna que existe para cuando la fuente de datos SI lo traiga).
"""
from alembic import op

revision = "0015_ip_reputation_cache"
down_revision = "0014_rate_limits_and_quotas"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE security_ip_reputation (
            network_or_ip INET NOT NULL PRIMARY KEY,
            abuse_confidence SMALLINT NOT NULL,
            total_reports INTEGER NULL,
            country_code VARCHAR(2) NULL,
            source VARCHAR(30) NOT NULL,
            fetched_at TIMESTAMP NOT NULL,
            expires_at TIMESTAMP NOT NULL
        )
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS security_ip_reputation")
