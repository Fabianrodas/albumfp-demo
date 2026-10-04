"""amanecer y atardecer en media_context

Revision ID: 0006_solar_context
Revises: 0005_share_capabilities
Create Date: 2026-08-23

Va en `media_context` y no en una tabla nueva porque es exactamente lo mismo
que ya vive ahi: un dato que un proveedor externo DERIVA de las coordenadas
del recuerdo. Comparte fila, y por tanto se lee de una sola pasada con el
lugar.

`sunrise_at`/`sunset_at` son TIMESTAMP sin zona, como el resto del esquema
(ver `NaiveDatetimeJSONProvider`): guardan hora local ya convertida, no
instantes UTC, para que se comparen directamente con `media.taken_at` y se
pinten tal cual. El plan del roadmap decia TIMESTAMPTZ; se cambio a
proposito, porque una columna con zona en este esquema volveria a producir el
desplazamiento horario que arreglo la fase 02.

`latitude`/`longitude` guardan la coordenada QUE PRODUJO este contexto, que
hasta ahora no se persistia en ningun sitio salvo `media_exif`. Sin ellas,
una foto ubicada con el pin manual no tenia forma de volver a consultar nada
(ni el sol, ni un refresh del lugar): el plan asumia que las coordenadas
siempre venian del EXIF, y desde la fase de pin manual eso dejo de ser cierto.
"""
from alembic import op

revision = "0006_solar_context"
down_revision = "0005_share_capabilities"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE media_context
            ADD COLUMN latitude NUMERIC(9,6) NULL,
            ADD COLUMN longitude NUMERIC(9,6) NULL,
            ADD COLUMN sunrise_at TIMESTAMP NULL,
            ADD COLUMN sunset_at TIMESTAMP NULL,
            ADD COLUMN solar_provider VARCHAR(40) NULL,
            ADD COLUMN solar_enriched_at TIMESTAMP NULL
        """
    )
    # Las fotos ya enriquecidas solo tenian coordenadas si venian del EXIF;
    # rellenarlas deja que el boton solar funcione tambien para ellas.
    op.execute(
        """
        UPDATE media_context mc
        SET latitude = me.latitude, longitude = me.longitude
        FROM media_exif me
        WHERE me.media_id = mc.media_id
          AND me.latitude IS NOT NULL
          AND me.longitude IS NOT NULL
        """
    )


def downgrade():
    op.execute(
        """
        ALTER TABLE media_context
            DROP COLUMN solar_enriched_at,
            DROP COLUMN solar_provider,
            DROP COLUMN sunset_at,
            DROP COLUMN sunrise_at,
            DROP COLUMN longitude,
            DROP COLUMN latitude
        """
    )
