"""asset comments: plain text tied to the durable asset identity (F04)

Revision ID: 0029_asset_comments
Revises: 0028_webauthn_passkeys
Create Date: 2026-09-24

Un comentario pertenece al ASSET (identidad durable desde L10A), no a un
álbum: sobrevive a archivar, a la papelera y a los cambios de pertenencia, y
solo desaparece si el asset se borra para siempre (ON DELETE CASCADE) o si
se borra la cuenta de quien lo escribió. Texto plano: el CHECK replica el
contrato del servidor (recortado, 1..2000 caracteres); nunca HTML.
"""
from alembic import op


revision = "0029_asset_comments"
down_revision = "0028_webauthn_passkeys"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE asset_comments (
            id BIGSERIAL PRIMARY KEY,
            asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            body TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NULL,
            CONSTRAINT chk_asset_comments_body CHECK (
                char_length(body) BETWEEN 1 AND 2000 AND body = btrim(body, E' \\t\\r\\n'))
        )
        """
    )
    op.execute("CREATE INDEX idx_asset_comments_asset ON asset_comments (asset_id, created_at, id)")
    op.execute("CREATE INDEX idx_asset_comments_user ON asset_comments (user_id)")


def downgrade():
    # Los comentarios son datos de las personas: bajar con alguno dentro los
    # borraría sin avisar. Se para en seco.
    guardados = op.get_bind().exec_driver_sql("SELECT COUNT(*) FROM asset_comments").scalar()
    if guardados:
        raise RuntimeError(
            f"No se puede bajar de 0029: hay {guardados} comentario(s) en asset_comments. "
            "Bajar ahora los borraría; expórtalos o elimínalos a propósito antes."
        )
    op.execute("DROP TABLE asset_comments")
