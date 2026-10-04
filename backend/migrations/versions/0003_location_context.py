"""media_context e integration_usage

Revision ID: 0003_location_context
Revises: 0002_media_exif
Create Date: 2026-08-21

`media_context` guarda lo que un proveedor de terceros DERIVA de las
coordenadas EXIF (lugar, region, pais...). Va separada de `media_exif` a
proposito: el EXIF es lo que trae la foto, esto es lo que un servicio externo
respondio sobre esas coordenadas, y solo se llena cuando el dueno pide
explicitamente "Completar contexto". `location_enriched_at` es la marca de
cache: si no es NULL, no se vuelve a llamar al proveedor salvo refresh
explicito.

`integration_usage` es generica y reutilizable por CUALQUIER proveedor con
cuota diaria (LocationIQ hoy; Visual Crossing, OCR.Space e Imagga mas
adelante), no solo LocationIQ: por eso la clave es (provider, period_key) y no
hay ninguna columna especifica de un proveedor.
"""
from alembic import op

revision = "0003_location_context"
down_revision = "0002_media_exif"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE media_context (
            media_id INTEGER PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
            place_display_name TEXT NULL,
            locality VARCHAR(160) NULL,
            region VARCHAR(160) NULL,
            country_code VARCHAR(2) NULL,
            country_name VARCHAR(120) NULL,
            timezone VARCHAR(80) NULL,
            location_provider VARCHAR(40) NULL,
            location_enriched_at TIMESTAMP NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE TABLE integration_usage (
            provider VARCHAR(40) NOT NULL,
            period_key VARCHAR(20) NOT NULL,
            request_count INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (provider, period_key)
        )
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS integration_usage")
    op.execute("DROP TABLE IF EXISTS media_context")
