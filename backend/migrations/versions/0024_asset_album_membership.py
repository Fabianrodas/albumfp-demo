"""assets and album membership (L10A)

Revision ID: 0024_asset_album_membership
Revises: 0023_media_archive
Create Date: 2026-09-21

`media` pasa a llamarse `assets` sin tocar una sola fila: al renombrar la
tabla, cada id, cada clave foranea (EXIF, contexto, OCR, metadata, tags,
portada), cada trigger y cada indice la siguen por OID. La pertenencia a un
album sale de la columna `album_id` a `album_assets`, una fila por cada media
existente. Es trabajo solo de base: ningun objeto de almacenamiento se
renombra, se copia ni se borra.
"""
from alembic import op


revision = "0024_asset_album_membership"
down_revision = "0023_media_archive"
branch_labels = None
depends_on = None


# Las dos funciones de 0019 nombran la tabla DENTRO de su cuerpo: renombrarla
# sin reescribirlas dejaria el indice de busqueda roto en la primera edicion.
REFRESH_FUNCTION = """
CREATE OR REPLACE FUNCTION albumfp_refresh_media_search(target_id INTEGER)
RETURNS VOID
LANGUAGE SQL
VOLATILE
AS $$
    UPDATE __TABLE__ AS m
    SET search_title = albumfp_search_normalize(m.title),
        search_metadata = albumfp_search_normalize(concat_ws(' ',
            m.caption,
            (SELECT mm.original_filename FROM media_metadata mm WHERE mm.media_id = m.id),
            (SELECT string_agg(t.name, ' ' ORDER BY t.name)
             FROM media_tags mt JOIN tags t ON t.id = mt.tag_id
             WHERE mt.media_id = m.id),
            (SELECT concat_ws(' ', mc.place_display_name, mc.locality, mc.region,
                                      mc.country_code, mc.country_name)
             FROM media_context mc WHERE mc.media_id = m.id),
            (SELECT concat_ws(' ', me.camera_make, me.camera_model)
             FROM media_exif me WHERE me.media_id = m.id)
        )),
        search_place = albumfp_search_normalize((
            SELECT concat_ws(' ', mc.place_display_name, mc.locality, mc.region,
                                   mc.country_code, mc.country_name)
            FROM media_context mc WHERE mc.media_id = m.id
        )),
        search_ocr = albumfp_search_normalize((
            SELECT mo.extracted_text FROM media_ocr mo WHERE mo.media_id = m.id
        ))
    WHERE m.id = target_id;
$$;
"""

TRIGGER_FUNCTION = """
CREATE OR REPLACE FUNCTION albumfp_refresh_media_search_trigger()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    target_media_id INTEGER;
    linked_media_id INTEGER;
BEGIN
    IF TG_TABLE_NAME = 'tags' THEN
        FOR linked_media_id IN
            SELECT mt.media_id FROM media_tags mt WHERE mt.tag_id = NEW.id
        LOOP
            PERFORM albumfp_refresh_media_search(linked_media_id);
        END LOOP;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME = '__TABLE__' THEN
        target_media_id := COALESCE(NEW.id, OLD.id);
    ELSIF TG_OP = 'DELETE' THEN
        target_media_id := OLD.media_id;
    ELSIF TG_OP = 'UPDATE' AND OLD.media_id IS DISTINCT FROM NEW.media_id THEN
        PERFORM albumfp_refresh_media_search(OLD.media_id);
        target_media_id := NEW.media_id;
    ELSE
        target_media_id := NEW.media_id;
    END IF;
    PERFORM albumfp_refresh_media_search(target_media_id);
    RETURN COALESCE(NEW, OLD);
END;
$$;
"""

CREATE_ALBUM_ASSETS = """
CREATE TABLE album_assets (
    album_id INTEGER NOT NULL,
    asset_id INTEGER NOT NULL,
    owner_id INTEGER NOT NULL,
    position INTEGER NULL,
    added_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT album_assets_pkey PRIMARY KEY (album_id, asset_id),
    CONSTRAINT fk_album_assets_album FOREIGN KEY (album_id, owner_id)
        REFERENCES albums (id, user_id) ON DELETE CASCADE,
    CONSTRAINT fk_album_assets_asset FOREIGN KEY (asset_id, owner_id)
        REFERENCES assets (id, user_id) ON DELETE CASCADE
)
"""


def _rename_table(bind, old: str, new: str) -> None:
    """Tabla, secuencia y clave primaria, para que coincidan con lo que crea
    `schema.sql` en una instalacion desde cero. Los nombres se leen del
    catalogo en vez de suponerse."""
    secuencia = bind.exec_driver_sql(f"SELECT pg_get_serial_sequence('{old}', 'id')").scalar()
    primaria = bind.exec_driver_sql(
        f"SELECT conname FROM pg_constraint WHERE conrelid = '{old}'::regclass AND contype = 'p'"
    ).scalar()
    op.execute(f"ALTER TABLE {old} RENAME TO {new}")
    if secuencia:
        op.execute(f"ALTER SEQUENCE {secuencia} RENAME TO {new}_id_seq")
    if primaria:
        op.execute(f"ALTER TABLE {new} RENAME CONSTRAINT {primaria} TO {new}_pkey")


def upgrade():
    bind = op.get_bind()
    version = int(bind.exec_driver_sql("SHOW server_version_num").scalar())
    if version < 150000:
        raise RuntimeError(
            f"0024 necesita PostgreSQL 15 o superior (ON DELETE SET NULL por columna); "
            f"este servidor es {version}. No se cambió nada."
        )
    cruzados = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM media m JOIN albums a ON a.id = m.album_id WHERE m.user_id <> a.user_id"
    ).scalar()
    if cruzados:
        raise RuntimeError(
            f"No se puede subir a 0024: hay {cruzados} media cuyo dueño no es el dueño de su álbum. "
            "Un asset solo puede pertenecer a álbumes de su propio dueño; corrígelas antes de migrar."
        )

    _rename_table(bind, "media", "assets")
    op.execute(REFRESH_FUNCTION.replace("__TABLE__", "assets"))
    op.execute(TRIGGER_FUNCTION.replace("__TABLE__", "assets"))

    # (id, user_id) es unico porque id ya lo es; hace falta declararlo para que
    # la pertenencia pueda exigir, en el propio esquema, que album y asset
    # sean del mismo dueño.
    op.execute("ALTER TABLE assets ADD CONSTRAINT uq_assets_id_owner UNIQUE (id, user_id)")
    op.execute("ALTER TABLE albums ADD CONSTRAINT uq_albums_id_owner UNIQUE (id, user_id)")
    op.execute(CREATE_ALBUM_ASSETS)
    op.execute("CREATE INDEX idx_album_assets_asset ON album_assets (asset_id)")
    op.execute(
        """
        INSERT INTO album_assets (album_id, asset_id, owner_id, added_at)
        SELECT album_id, id, user_id, COALESCE(created_at, CURRENT_TIMESTAMP) FROM assets
        """
    )

    # Una portada que apunta fuera de su album ya no la mostraba ninguna
    # lectura (todas exigian cm.album_id = a.id): vaciarla no cambia lo que se
    # ve, y es lo que permite que el esquema la exija de aqui en adelante.
    fuera = bind.exec_driver_sql(
        """
        SELECT COUNT(*) FROM albums a
        WHERE a.cover_media_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM album_assets aa WHERE aa.album_id = a.id AND aa.asset_id = a.cover_media_id)
        """
    ).scalar()
    if fuera:
        op.execute(
            """
            UPDATE albums a SET cover_media_id = NULL
            WHERE a.cover_media_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM album_assets aa WHERE aa.album_id = a.id AND aa.asset_id = a.cover_media_id)
            """
        )
        print(f"0024: {fuera} portada(s) fuera de su álbum se vaciaron; ninguna lectura las mostraba.")
    op.execute(
        """
        ALTER TABLE albums ADD CONSTRAINT fk_album_cover_member
        FOREIGN KEY (id, cover_media_id) REFERENCES album_assets (album_id, asset_id)
        ON DELETE SET NULL (cover_media_id)
        """
    )

    op.execute("DROP INDEX IF EXISTS idx_media_album_active_created")
    op.execute("DROP INDEX IF EXISTS idx_media_album")
    op.execute("ALTER TABLE assets DROP CONSTRAINT IF EXISTS fk_media_album")
    op.execute("ALTER TABLE assets DROP COLUMN album_id")


def downgrade():
    # 0023 solo sabe guardar UN album por archivo. Bajar con un asset en varios
    # albumes, o en ninguno, perderia esa informacion en silencio: se para en
    # seco antes de tocar nada.
    bind = op.get_bind()
    varios = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM (SELECT asset_id FROM album_assets GROUP BY asset_id HAVING COUNT(*) > 1) x"
    ).scalar()
    sueltos = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM assets a WHERE NOT EXISTS (SELECT 1 FROM album_assets aa WHERE aa.asset_id = a.id)"
    ).scalar()
    if varios or sueltos:
        problemas = []
        if varios:
            problemas.append(f"{varios} asset(s) en varios álbumes")
        if sueltos:
            problemas.append(f"{sueltos} asset(s) sin álbum")
        raise RuntimeError(
            "No se puede bajar de 0024: " + " y ".join(problemas) + ". "
            "0023 solo representa un álbum por archivo; deja cada asset en exactamente un álbum antes de bajar."
        )

    op.execute("ALTER TABLE assets ADD COLUMN album_id INTEGER")
    op.execute("UPDATE assets a SET album_id = aa.album_id FROM album_assets aa WHERE aa.asset_id = a.id")
    op.execute("ALTER TABLE assets ALTER COLUMN album_id SET NOT NULL")
    op.execute(
        "ALTER TABLE assets ADD CONSTRAINT fk_media_album "
        "FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE CASCADE"
    )
    op.execute("CREATE INDEX idx_media_album ON assets(album_id)")
    op.execute("CREATE INDEX idx_media_album_active_created ON assets(album_id, deleted_at, created_at DESC)")

    op.execute("ALTER TABLE albums DROP CONSTRAINT fk_album_cover_member")
    op.execute("DROP TABLE album_assets")
    op.execute("ALTER TABLE albums DROP CONSTRAINT uq_albums_id_owner")
    op.execute("ALTER TABLE assets DROP CONSTRAINT uq_assets_id_owner")

    _rename_table(bind, "assets", "media")
    op.execute(REFRESH_FUNCTION.replace("__TABLE__", "media"))
    op.execute(TRIGGER_FUNCTION.replace("__TABLE__", "media"))
