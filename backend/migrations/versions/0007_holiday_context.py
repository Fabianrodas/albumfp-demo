"""festivos en media_context + cache por pais y año

Revision ID: 0007_holiday_context
Revises: 0006_solar_context
Create Date: 2026-08-23

`holiday_cache` existe porque Nager.Date no consulta por dia: da el año
entero de un pais. Cachearlo por (pais, año) convierte "¿fue festivo este
recuerdo?" en una busqueda local para todos los recuerdos siguientes de ese
mismo año y pais, que en una biblioteca personal son casi todos.

`holiday_enriched_at` se rellena tambien cuando NO hubo festivo: sin esa
marca, cada recuerdo de un dia normal volveria a preguntar para siempre.
"""
from alembic import op

revision = "0007_holiday_context"
down_revision = "0006_solar_context"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE media_context
            ADD COLUMN holiday_name VARCHAR(200) NULL,
            ADD COLUMN holiday_type VARCHAR(80) NULL,
            ADD COLUMN holiday_provider VARCHAR(40) NULL,
            ADD COLUMN holiday_enriched_at TIMESTAMP NULL
        """
    )
    op.execute(
        """
        CREATE TABLE holiday_cache (
            country_code VARCHAR(2) NOT NULL,
            year INTEGER NOT NULL,
            payload JSONB NOT NULL,
            fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (country_code, year)
        )
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS holiday_cache")
    op.execute(
        """
        ALTER TABLE media_context
            DROP COLUMN holiday_enriched_at,
            DROP COLUMN holiday_provider,
            DROP COLUMN holiday_type,
            DROP COLUMN holiday_name
        """
    )
