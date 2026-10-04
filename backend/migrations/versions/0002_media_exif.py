"""media_exif: los metadatos EXIF de cada foto

Revision ID: 0002_media_exif
Revises: 0001_current_schema_baseline
Create Date: 2026-08-20

Tabla 1:1 con `media`, separada a proposito en vez de meter siete columnas mas
en `media`: solo las fotos tienen EXIF, y la mayoria de consultas de la app no
lo necesitan.

El indice es **parcial**: las filas sin coordenadas no ocupan sitio en el, que
es lo que interesa cuando muchas fotos no traen GPS.

SQL explicito en vez de `op.create_table`, para que esto y `schemas/schema.sql`
se puedan leer en paralelo y comprobar que dicen lo mismo.
"""
from alembic import op

revision = "0002_media_exif"
down_revision = "0001_current_schema_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE media_exif (
            media_id INTEGER PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
            taken_at_original TIMESTAMP NULL,
            latitude NUMERIC(9,6) NULL,
            longitude NUMERIC(9,6) NULL,
            altitude_m NUMERIC(9,2) NULL,
            camera_make VARCHAR(120) NULL,
            camera_model VARCHAR(120) NULL,
            orientation INTEGER NULL,
            extracted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_media_exif_coords ON media_exif(latitude, longitude)
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_media_exif_coords")
    op.execute("DROP TABLE IF EXISTS media_exif")
