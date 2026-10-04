"""texto detectado en una foto (OCR.Space)

Revision ID: 0009_media_ocr
Revises: 0008_weather_context
Create Date: 2026-08-25

Tabla aparte y 1:1 con `media`, como `media_exif`: solo unas pocas fotos van
a tener texto detectado (hay que pedirlo a mano) y ninguna consulta normal de
la app lo necesita. Una columna `TEXT` en `media` la cargaria en cada
listado.

El indice es de busqueda por contenido: la unica consulta que lee esta tabla
sin saber el `media_id` es el `q` de la busqueda existente, que hace
`LOWER(extracted_text) LIKE '%algo%'`. `pg_trgm` haria eso rapido de verdad,
pero es una extension que hay que instalar en el servidor; con una biblioteca
personal el escaneo secuencial sobre las pocas filas con OCR es de sobra.
"""
from alembic import op

revision = "0009_media_ocr"
down_revision = "0008_weather_context"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE media_ocr (
            media_id INTEGER PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
            extracted_text TEXT NOT NULL,
            detected_language VARCHAR(40) NULL,
            provider VARCHAR(40) NOT NULL,
            analyzed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS media_ocr")
