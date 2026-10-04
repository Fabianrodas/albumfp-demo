"""búsqueda avanzada con full-text nativo e índices GIN

Revision ID: 0019_indexed_search
Revises: 0018_reversible_share_tokens
Create Date: 2026-09-17

Los campos normalizados viven en AlbumFP y se actualizan por triggers para que
cualquier escritura válida (incluidos OCR, tags y contexto) refresque el índice
en la misma transacción. No requiere privilegios de superusuario.
"""
from alembic import op


revision = "0019_indexed_search"
down_revision = "0018_reversible_share_tokens"
branch_labels = None
depends_on = None


NORMALIZE_FUNCTION = r"""
CREATE OR REPLACE FUNCTION albumfp_search_normalize(value TEXT)
RETURNS TEXT
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
RETURN trim(regexp_replace(
    translate(
        COALESCE(value, ''),
        'ABCDEFGHIJKLMNOPQRSTUVWXYZ' || 'ÆŒKİǅ' ||
        'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ' ||
        'áàäâãåāăąÁÀÄÂÃÅĀĂĄ' ||
        'çćčÇĆČ' || 'ďđĎĐ' ||
        'éèëêēĕėęěÉÈËÊĒĔĖĘĚ' || 'ğĞ' ||
        'íìïîīĭįıÍÌÏÎĪĬĮ' || 'łŁ' ||
        'ñńňÑŃŇ' || 'óòöôõøōŏőÓÒÖÔÕØŌŎŐ' ||
        'řŘ' || 'śšşŚŠŞ' || 'ťŤ' ||
        'úùüûūŭůűųÚÙÜÛŪŬŮŰŲ' || 'ýÿÝŸ' || 'žźżŽŹŻ' ||
        U&'\0300\0301\0302\0303\0304\0306\0307\0308\030A\030B\030C\0327\0328',
        'abcdefghijklmnopqrstuvwxyz' || 'æœkiǆ' ||
        'абвгдеёжзийклмнопрстуфхцчшщъыьэюя' ||
        repeat('a', 18) || repeat('c', 6) || repeat('d', 4) ||
        repeat('e', 18) || repeat('g', 2) || repeat('i', 15) || repeat('l', 2) || repeat('n', 6) ||
        repeat('o', 18) || repeat('r', 2) || repeat('s', 6) || repeat('t', 2) ||
        repeat('u', 18) || repeat('y', 4) || repeat('z', 6)
    ),
    '[^[:alnum:]_]+', ' ', 'g'
));
"""


REFRESH_FUNCTION = r"""
CREATE OR REPLACE FUNCTION albumfp_refresh_media_search(target_id INTEGER)
RETURNS VOID
LANGUAGE SQL
VOLATILE
AS $$
    UPDATE media AS m
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


TRIGGER_FUNCTION = r"""
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

    IF TG_TABLE_NAME = 'media' THEN
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


ALBUM_TRIGGER_FUNCTION = r"""
CREATE OR REPLACE FUNCTION albumfp_normalize_album_search_trigger()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.search_title := albumfp_search_normalize(NEW.titulo);
    RETURN NEW;
END;
$$;
"""


def upgrade():
    op.execute(NORMALIZE_FUNCTION)
    op.execute(
        """
        ALTER TABLE albums
            ADD COLUMN search_title TEXT NOT NULL DEFAULT '',
            ADD COLUMN search_vector TSVECTOR GENERATED ALWAYS AS (
                setweight(to_tsvector('simple', search_title), 'A')
            ) STORED
        """
    )
    op.execute(
        """
        ALTER TABLE media
            ADD COLUMN search_title TEXT NOT NULL DEFAULT '',
            ADD COLUMN search_metadata TEXT NOT NULL DEFAULT '',
            ADD COLUMN search_place TEXT NOT NULL DEFAULT '',
            ADD COLUMN search_ocr TEXT NOT NULL DEFAULT '',
            ADD COLUMN search_vector TSVECTOR GENERATED ALWAYS AS (
                setweight(to_tsvector('simple', search_title), 'A') ||
                setweight(to_tsvector('simple', search_metadata), 'B') ||
                setweight(to_tsvector('simple', search_ocr), 'D')
            ) STORED,
            ADD COLUMN search_place_vector TSVECTOR GENERATED ALWAYS AS (
                to_tsvector('simple', search_place)
            ) STORED
        """
    )
    op.execute(REFRESH_FUNCTION)
    op.execute(TRIGGER_FUNCTION)
    op.execute(ALBUM_TRIGGER_FUNCTION)

    op.execute("UPDATE albums SET search_title = albumfp_search_normalize(titulo)")
    op.execute("SELECT albumfp_refresh_media_search(id) FROM media")

    op.execute("CREATE INDEX idx_albums_search_vector ON albums USING GIN (search_vector)")
    op.execute("CREATE INDEX idx_albums_search_title_prefix ON albums (search_title text_pattern_ops)")
    op.execute("CREATE INDEX idx_media_search_vector ON media USING GIN (search_vector)")
    op.execute("CREATE INDEX idx_media_search_place_vector ON media USING GIN (search_place_vector)")
    op.execute("CREATE INDEX idx_media_search_title_prefix ON media (search_title text_pattern_ops)")
    op.execute("CREATE INDEX idx_media_search_owner_taken ON media (user_id, taken_at DESC) WHERE deleted_at IS NULL")
    op.execute("CREATE INDEX idx_media_search_owner_type ON media (user_id, file_type, created_at DESC) WHERE deleted_at IS NULL")
    op.execute("CREATE INDEX idx_media_search_owner_favorite ON media (user_id, created_at DESC) WHERE deleted_at IS NULL AND is_favorite = TRUE")

    op.execute(
        """
        CREATE TRIGGER trg_albums_search_refresh
        BEFORE INSERT OR UPDATE OF titulo ON albums
        FOR EACH ROW EXECUTE FUNCTION albumfp_normalize_album_search_trigger()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_media_search_refresh
        AFTER INSERT OR UPDATE OF title, caption ON media
        FOR EACH ROW EXECUTE FUNCTION albumfp_refresh_media_search_trigger()
        """
    )
    for table in ("media_metadata", "media_exif", "media_context", "media_ocr", "media_tags"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_search_refresh
            AFTER INSERT OR UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION albumfp_refresh_media_search_trigger()
            """
        )
    op.execute(
        """
        CREATE TRIGGER trg_tags_search_refresh
        AFTER UPDATE OF name ON tags
        FOR EACH ROW EXECUTE FUNCTION albumfp_refresh_media_search_trigger()
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_media_search_owner_favorite")
    op.execute("DROP INDEX IF EXISTS idx_media_search_owner_type")
    op.execute("DROP INDEX IF EXISTS idx_media_search_owner_taken")
    op.execute("DROP TRIGGER IF EXISTS trg_tags_search_refresh ON tags")
    for table in ("media_tags", "media_ocr", "media_context", "media_exif", "media_metadata"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_search_refresh ON {table}")
    op.execute("DROP TRIGGER IF EXISTS trg_media_search_refresh ON media")
    op.execute("DROP TRIGGER IF EXISTS trg_albums_search_refresh ON albums")
    op.execute("DROP FUNCTION IF EXISTS albumfp_normalize_album_search_trigger()")
    op.execute("DROP FUNCTION IF EXISTS albumfp_refresh_media_search_trigger()")
    op.execute("DROP FUNCTION IF EXISTS albumfp_refresh_media_search(INTEGER)")
    op.execute("ALTER TABLE media DROP COLUMN search_place_vector, DROP COLUMN search_vector, DROP COLUMN search_ocr, DROP COLUMN search_place, DROP COLUMN search_metadata, DROP COLUMN search_title")
    op.execute("ALTER TABLE albums DROP COLUMN search_vector, DROP COLUMN search_title")
    op.execute("DROP FUNCTION IF EXISTS albumfp_search_normalize(TEXT)")
