"""clima historico en media_context

Revision ID: 0008_weather_context
Revises: 0007_holiday_context
Create Date: 2026-08-24

Cuarto proveedor que cuelga de `media_context`, con la misma forma que los
tres anteriores: los datos, quien los dio y cuando. `weather_enriched_at` es
la marca de cache; el clima de un dia pasado no cambia, asi que una vez
guardado no se vuelve a pedir salvo `refresh` explicito.

Los numericos van como NUMERIC y no como float por la misma razon que las
coordenadas: son medidas con decimales conocidos y no se quieren sorpresas de
binario. Ojo al leerlos: psycopg2 los devuelve como Decimal, que no es
serializable a JSON (ver `_shape_weather` en app/api/media.py).
"""
from alembic import op

revision = "0008_weather_context"
down_revision = "0007_holiday_context"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE media_context
            ADD COLUMN weather_temp_c NUMERIC(5,2) NULL,
            ADD COLUMN weather_condition VARCHAR(160) NULL,
            ADD COLUMN weather_icon VARCHAR(40) NULL,
            ADD COLUMN weather_precip_mm NUMERIC(8,2) NULL,
            ADD COLUMN weather_wind_kph NUMERIC(7,2) NULL,
            ADD COLUMN weather_provider VARCHAR(40) NULL,
            ADD COLUMN weather_enriched_at TIMESTAMP NULL
        """
    )


def downgrade():
    op.execute(
        """
        ALTER TABLE media_context
            DROP COLUMN weather_enriched_at,
            DROP COLUMN weather_provider,
            DROP COLUMN weather_wind_kph,
            DROP COLUMN weather_precip_mm,
            DROP COLUMN weather_icon,
            DROP COLUMN weather_condition,
            DROP COLUMN weather_temp_c
        """
    )
