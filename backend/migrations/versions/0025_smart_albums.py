"""smart albums: saved owner-scoped search definitions (L11)

Revision ID: 0025_smart_albums
Revises: 0024_asset_album_membership
Create Date: 2026-09-23

Un álbum inteligente es una búsqueda guardada: título, descripción y un objeto
JSON de filtros ya validado por la aplicación (`normalize_saved_filters`).
No es un álbum: no vive en `albums`, no tiene filas en `album_assets` y nunca
guarda ni copia assets. Sus resultados se calculan al abrirlo.
"""
from alembic import op


revision = "0025_smart_albums"
down_revision = "0024_asset_album_membership"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE smart_albums (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            titulo VARCHAR(100) NOT NULL,
            descripcion TEXT NULL,
            filters JSONB NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NULL,
            CONSTRAINT chk_smart_albums_filters_object CHECK (jsonb_typeof(filters) = 'object')
        )
        """
    )
    # El listado del dueño, en el mismo orden que devuelve la API.
    op.execute("CREATE INDEX idx_smart_albums_user ON smart_albums (user_id, created_at DESC, id DESC)")


def downgrade():
    # Las definiciones son datos del usuario: borrar la tabla con filas dentro
    # las perdería sin avisar. Se para en seco; hay que borrarlas antes.
    guardadas = op.get_bind().exec_driver_sql("SELECT COUNT(*) FROM smart_albums").scalar()
    if guardadas:
        raise RuntimeError(
            f"No se puede bajar de 0025: hay {guardadas} álbum(es) inteligente(s) en smart_albums. "
            "Bórralos antes de bajar para no perder esas definiciones en silencio."
        )
    op.execute("DROP TABLE smart_albums")
