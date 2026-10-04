"""album activity + in-app notifications; notification preferences lose push (L12)

Revision ID: 0026_activity_notifications
Revises: 0025_smart_albums
Create Date: 2026-09-23

Dos historiales distintos, los dos escritos por el servidor dentro de la misma
transacción que la operación que los causa:

- `album_activity`: lo que pasó en un álbum normal (invitar, reclamar, subir,
  añadir/quitar un recuerdo, cambiar la portada, cambiar permisos). Pertenece
  al álbum: se va con él. Si el actor o el recuerdo desaparecen, el evento
  sigue siendo verdad y solo pierde la referencia.
- `notifications`: avisos personales in-app (invitación, invitación aceptada,
  foto nueva en un álbum compartido). Pertenecen al destinatario: se van con su
  cuenta. Si el álbum o el actor desaparecen, el aviso sigue ahí sin enlace.

Las preferencias dejan de hablar de push: el proveedor externo se retiró y
`web_push_enabled` (que por defecto era falso) habría silenciado todo aviso
in-app. Las tres categorías conservan exactamente su valor.
"""
from alembic import op


revision = "0026_activity_notifications"
down_revision = "0025_smart_albums"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE album_activity (
            id BIGSERIAL PRIMARY KEY,
            album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
            actor_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
            event_type VARCHAR(40) NOT NULL,
            subject_asset_id INTEGER NULL REFERENCES assets(id) ON DELETE SET NULL,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT chk_album_activity_event_type CHECK (event_type IN (
                'album_invite_created', 'share_claimed', 'asset_uploaded', 'asset_added',
                'asset_removed', 'cover_changed', 'share_permission_changed'
            )),
            CONSTRAINT chk_album_activity_metadata_object CHECK (jsonb_typeof(metadata_json) = 'object')
        )
        """
    )
    # El panel de un álbum, más nuevo primero, en el mismo orden que la API.
    op.execute("CREATE INDEX idx_album_activity_album ON album_activity (album_id, created_at DESC, id DESC)")

    op.execute(
        """
        CREATE TABLE notifications (
            id BIGSERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            event_type VARCHAR(40) NOT NULL,
            album_id INTEGER NULL REFERENCES albums(id) ON DELETE SET NULL,
            actor_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
            read_at TIMESTAMP NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT chk_notifications_event_type CHECK (event_type IN (
                'album_invite', 'share_claimed', 'shared_album_upload'
            ))
        )
        """
    )
    # La campana: lista más nueva primero, y el contador de no leídas.
    op.execute("CREATE INDEX idx_notifications_user ON notifications (user_id, created_at DESC, id DESC)")
    op.execute("CREATE INDEX idx_notifications_unread ON notifications (user_id) WHERE read_at IS NULL")

    op.execute("ALTER TABLE user_notification_preferences DROP COLUMN web_push_enabled")


def downgrade():
    # Actividad y avisos son historial que el usuario ya vio: borrar las tablas
    # con filas lo perdería sin avisar. Se para en seco; hay que vaciarlas antes.
    bind = op.get_bind()
    for tabla in ("album_activity", "notifications"):
        filas = bind.exec_driver_sql(f"SELECT COUNT(*) FROM {tabla}").scalar()
        if filas:
            raise RuntimeError(
                f"No se puede bajar de 0026: hay {filas} fila(s) en {tabla}. "
                "Bórralas antes de bajar para no perder ese historial en silencio."
            )
    op.execute("DROP TABLE notifications")
    op.execute("DROP TABLE album_activity")
    # Vuelve la bandera de la 0010 con su default de entonces; las categorías
    # conservan su valor.
    op.execute(
        "ALTER TABLE user_notification_preferences "
        "ADD COLUMN web_push_enabled BOOLEAN NOT NULL DEFAULT FALSE"
    )
