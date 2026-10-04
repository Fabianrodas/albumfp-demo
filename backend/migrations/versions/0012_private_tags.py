"""cada vocabulario de etiquetas pertenece a un dueno

Revision ID: 0012_private_tags
Revises: 0011_media_previews
Create Date: 2026-08-28

`tags.name` era UNIQUE **global**: una sola fila "vacaciones" para toda la
instalacion. Dos consecuencias, y las dos son fugas de metadata entre cuentas:

1. `GET /api/tags` devolvia el catalogo entero, asi que cualquier cuenta
   (incluida una recien creada que no ha subido nada) leia los nombres de las
   etiquetas de todos los demas.
2. Si A ya habia creado "playa", B no podia crear la suya: `ON CONFLICT (name)`
   le devolvia **la fila de A**, y a partir de ahi los dos etiquetaban con el
   mismo id.

El backfill deriva el dueno del sitio donde de verdad vive el dato:
`media_tags -> media -> albums.user_id`. Una etiqueta usada por varios duenos
no se puede repartir, asi que se **duplica** una fila por dueno y se repuntan
sus `media_tags` — nadie pierde una etiqueta que ya tenia puesta.

Las que no usa ninguna foto se quedan con `created_by` si ese usuario sigue
existiendo (`created_by` no tiene FK, puede apuntar a nadie). Si no hay dueno
derivable, la fila se borra: es una etiqueta sin fotos y sin autor conocido,
no hay a quien darsela y conservarla en un modelo por dueno no significa nada.

`created_by` se conserva y NO es redundante con `owner_id`: desde esta fase un
colaborador con `organize` puede crear una etiqueta dentro del album de otro,
y esa etiqueta nace del dueno del album aunque la escribiera el colaborador.
"""
from alembic import op

revision = "0012_private_tags"
down_revision = "0011_media_previews"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE tags ADD COLUMN owner_id INTEGER")

    # El UNIQUE global debe caer ANTES de clonar: clonar "playa" para un
    # segundo dueno lo violaria. Si por lo que sea no existiera con este
    # nombre, el INSERT de mas abajo falla y Postgres deshace la migracion
    # entera; nunca queda a medias.
    op.execute("ALTER TABLE tags DROP CONSTRAINT IF EXISTS tags_name_key")

    # Cada pareja (etiqueta, dueno que la usa) que existe de verdad hoy.
    op.execute(
        """
        CREATE TEMP TABLE tag_owner_pairs AS
        SELECT DISTINCT mt.tag_id, a.user_id AS owner_id
        FROM media_tags mt
        JOIN media m ON m.id = mt.media_id
        JOIN albums a ON a.id = m.album_id
        """
    )

    # La fila original se queda con uno de sus duenos (el menor id, da igual
    # cual mientras sea determinista).
    op.execute(
        """
        UPDATE tags t
        SET owner_id = p.owner_id
        FROM (SELECT tag_id, MIN(owner_id) AS owner_id FROM tag_owner_pairs GROUP BY tag_id) p
        WHERE t.id = p.tag_id
        """
    )

    op.execute("CREATE TEMP TABLE tag_clones (old_tag_id INTEGER, owner_id INTEGER, new_tag_id INTEGER)")
    op.execute(
        """
        WITH extra AS (
            SELECT p.tag_id AS old_tag_id, p.owner_id, t.name, t.created_at, t.created_by
            FROM tag_owner_pairs p
            JOIN tags t ON t.id = p.tag_id
            WHERE p.owner_id <> t.owner_id
        ), insertadas AS (
            INSERT INTO tags (name, created_at, created_by, owner_id)
            SELECT name, created_at, created_by, owner_id FROM extra
            RETURNING id, name, owner_id
        )
        INSERT INTO tag_clones (old_tag_id, owner_id, new_tag_id)
        SELECT e.old_tag_id, i.owner_id, i.id
        FROM insertadas i
        JOIN extra e ON e.name = i.name AND e.owner_id = i.owner_id
        """
    )

    # Cada asociacion apunta ahora a la etiqueta de SU dueno. El nuevo id es
    # recien creado, asi que no puede chocar con la PK (media_id, tag_id).
    op.execute(
        """
        UPDATE media_tags mt
        SET tag_id = c.new_tag_id
        FROM tag_clones c, media m, albums a
        WHERE mt.tag_id = c.old_tag_id
          AND mt.media_id = m.id
          AND m.album_id = a.id
          AND a.user_id = c.owner_id
        """
    )

    # Sin uso: el autor, si todavia existe.
    op.execute(
        """
        UPDATE tags t
        SET owner_id = t.created_by
        WHERE t.owner_id IS NULL
          AND t.created_by IS NOT NULL
          AND EXISTS (SELECT 1 FROM users u WHERE u.id = t.created_by)
        """
    )
    # Lo que sigue sin dueno no lo usa ninguna foto (si la usara, el paso de
    # arriba le habria puesto uno) y su autor ya no existe.
    op.execute("DELETE FROM tags WHERE owner_id IS NULL")

    op.execute("DROP TABLE tag_owner_pairs")
    op.execute("DROP TABLE tag_clones")

    op.execute(
        """
        ALTER TABLE tags
            ALTER COLUMN owner_id SET NOT NULL,
            ADD CONSTRAINT fk_tags_owner FOREIGN KEY (owner_id)
                REFERENCES users(id) ON DELETE CASCADE,
            ADD CONSTRAINT uq_tags_owner_name UNIQUE (owner_id, name)
        """
    )
    op.execute("CREATE INDEX idx_tags_owner ON tags(owner_id)")


def downgrade():
    # Volver al UNIQUE global exige que cada nombre exista una sola vez. Si dos
    # cuentas tienen "playa", fusionarlas significaria repuntar las fotos de una
    # al vocabulario de la otra: exactamente la fuga que esta fase arregla, y
    # ademas irreversible. Se para en seco en vez de corromper en silencio.
    conexion = op.get_bind()
    duplicados = conexion.exec_driver_sql(
        "SELECT name, COUNT(*) FROM tags GROUP BY name HAVING COUNT(*) > 1"
    ).fetchall()
    if duplicados:
        nombres = ", ".join(fila[0] for fila in duplicados[:5])
        raise RuntimeError(
            "No se puede bajar de 0012: hay nombres de etiqueta repetidos entre cuentas "
            f"({nombres}...). Fusionarlos mezclaria las bibliotecas de dos usuarios. "
            "Resuelve los duplicados a mano antes de bajar."
        )

    op.execute("DROP INDEX IF EXISTS idx_tags_owner")
    op.execute(
        """
        ALTER TABLE tags
            DROP CONSTRAINT IF EXISTS uq_tags_owner_name,
            DROP CONSTRAINT IF EXISTS fk_tags_owner,
            DROP COLUMN IF EXISTS owner_id
        """
    )
    op.execute("ALTER TABLE tags ADD CONSTRAINT tags_name_key UNIQUE (name)")
