"""preferencias de notificaciones push (OneSignal)

Revision ID: 0010_notification_preferences
Revises: 0009_media_ocr
Create Date: 2026-08-27

Una fila por usuario, 1:1 con `users` como `media_exif` lo es con `media`.
Sin fila = valores por defecto (notificaciones apagadas); el codigo que lee
esta tabla hace ese merge, asi que no hace falta sembrar una fila por cada
cuenta existente al migrar.
"""
from alembic import op

revision = "0010_notification_preferences"
down_revision = "0009_media_ocr"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE user_notification_preferences (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            web_push_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            notify_album_invites BOOLEAN NOT NULL DEFAULT TRUE,
            notify_share_claimed BOOLEAN NOT NULL DEFAULT TRUE,
            notify_shared_album_uploads BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS user_notification_preferences")
