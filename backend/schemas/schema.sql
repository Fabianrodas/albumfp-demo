-- Normalizacion estable para busqueda, sin depender de extensiones del
-- servidor. El cliente aplica el mismo contrato a cada consulta.
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

-- =========================
-- USERS
-- =========================
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    full_name VARCHAR(120),
    avatar_path TEXT,
    password_hash TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    last_login TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NULL,
    created_by INTEGER NULL,
    updated_by INTEGER NULL
);

-- =========================
-- ALBUMS
-- =========================
CREATE TABLE albums (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    titulo VARCHAR(100) NOT NULL,
    descripcion TEXT,
    active BOOLEAN DEFAULT TRUE,
    is_private BOOLEAN DEFAULT TRUE,
    cover_media_id INTEGER NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NULL,
    created_by INTEGER NULL,
    updated_by INTEGER NULL,
    search_title TEXT NOT NULL DEFAULT '',
    search_vector TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', search_title), 'A')
    ) STORED,

    CONSTRAINT fk_album_user FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE CASCADE,

    -- Redundante con la PK, pero declarado: es lo que deja que `album_assets`
    -- exija en el propio esquema que album y asset sean del mismo dueño.
    CONSTRAINT uq_albums_id_owner UNIQUE (id, user_id)
);

-- =========================
-- ASSETS (L10A: antes `media`)
-- =========================
-- El archivo del dueño. `user_id` es el dueño; `created_by`, quien lo subió.
-- La pertenencia a álbumes vive en `album_assets`: un asset puede estar en
-- cero, uno o varios álbumes del mismo dueño.
CREATE TABLE assets (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    storage_path TEXT NOT NULL,
    file_type VARCHAR(10) NOT NULL,
    title VARCHAR(120) NOT NULL,
    caption TEXT,
    is_favorite BOOLEAN DEFAULT FALSE,
    taken_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER NULL,
    deleted_at TIMESTAMP NULL,
    archived_at TIMESTAMP NULL,
    search_title TEXT NOT NULL DEFAULT '',
    search_metadata TEXT NOT NULL DEFAULT '',
    search_place TEXT NOT NULL DEFAULT '',
    search_ocr TEXT NOT NULL DEFAULT '',
    search_vector TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', search_title), 'A') ||
        setweight(to_tsvector('simple', search_metadata), 'B') ||
        setweight(to_tsvector('simple', search_ocr), 'D')
    ) STORED,
    search_place_vector TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('simple', search_place)
    ) STORED,

    CONSTRAINT fk_media_user FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE CASCADE,

    CONSTRAINT chk_file_type CHECK (file_type IN ('image', 'video')),

    CONSTRAINT uq_assets_id_owner UNIQUE (id, user_id)
);

-- =========================
-- ALBUM ASSETS (pertenencia N:M)
-- =========================
-- Las dos claves compuestas hacen imposible una pertenencia entre dueños
-- distintos. `position` queda reservado para un orden manual (NULL = orden
-- natural). Borrar un álbum borra solo sus pertenencias, nunca los assets.
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
);

-- =========================
-- FIX FK CIRCULAR (cover_media)
-- =========================
ALTER TABLE albums
ADD CONSTRAINT fk_cover_media
FOREIGN KEY (cover_media_id)
REFERENCES assets(id)
ON DELETE SET NULL;

-- La portada siempre es un miembro del álbum; quitar esa pertenencia la vacía.
ALTER TABLE albums
ADD CONSTRAINT fk_album_cover_member
FOREIGN KEY (id, cover_media_id) REFERENCES album_assets (album_id, asset_id)
ON DELETE SET NULL (cover_media_id);

-- =========================
-- MEDIA METADATA (1:1)
-- =========================
-- =========================
-- EXIF (1:1 con media, solo fotos)
-- =========================
CREATE TABLE media_exif (
    media_id INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    taken_at_original TIMESTAMP NULL,
    latitude NUMERIC(9,6) NULL,
    longitude NUMERIC(9,6) NULL,
    altitude_m NUMERIC(9,2) NULL,
    camera_make VARCHAR(120) NULL,
    camera_model VARCHAR(120) NULL,
    orientation INTEGER NULL,
    extracted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =========================
-- Contexto derivado de terceros (1:1 con media, solo cuando se pide)
-- =========================
CREATE TABLE media_context (
    media_id INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    place_display_name TEXT NULL,
    locality VARCHAR(160) NULL,
    region VARCHAR(160) NULL,
    country_code VARCHAR(2) NULL,
    country_name VARCHAR(120) NULL,
    timezone VARCHAR(80) NULL,
    location_provider VARCHAR(40) NULL,
    location_enriched_at TIMESTAMP NULL,
    latitude NUMERIC(9,6) NULL,
    longitude NUMERIC(9,6) NULL,
    sunrise_at TIMESTAMP NULL,
    sunset_at TIMESTAMP NULL,
    solar_provider VARCHAR(40) NULL,
    solar_enriched_at TIMESTAMP NULL,
    holiday_name VARCHAR(200) NULL,
    holiday_type VARCHAR(80) NULL,
    holiday_provider VARCHAR(40) NULL,
    holiday_enriched_at TIMESTAMP NULL,
    weather_temp_c NUMERIC(5,2) NULL,
    weather_condition VARCHAR(160) NULL,
    weather_icon VARCHAR(40) NULL,
    weather_precip_mm NUMERIC(8,2) NULL,
    weather_wind_kph NUMERIC(7,2) NULL,
    weather_provider VARCHAR(40) NULL,
    weather_enriched_at TIMESTAMP NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Texto detectado dentro de una foto (OCR.Space). Tabla aparte y 1:1 como
-- media_exif: hay que pedirlo a mano, asi que casi ninguna fila existe, y
-- ninguna consulta normal de la app necesita el texto.
CREATE TABLE media_ocr (
    media_id INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    extracted_text TEXT NOT NULL,
    detected_language VARCHAR(40) NULL,
    provider VARCHAR(40) NOT NULL,
    analyzed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Nager.Date solo consulta por año completo, no por dia: cachearlo por
-- (pais, año) evita una llamada por cada recuerdo de ese mismo año.
CREATE TABLE holiday_cache (
    country_code VARCHAR(2) NOT NULL,
    year INTEGER NOT NULL,
    payload JSONB NOT NULL,
    fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (country_code, year)
);

-- Generica: (provider, period_key) sirve para cualquier API con cuota diaria,
-- no solo LocationIQ.
CREATE TABLE integration_usage (
    provider VARCHAR(40) NOT NULL,
    period_key VARCHAR(20) NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (provider, period_key)
);

CREATE TABLE media_metadata (
    id SERIAL PRIMARY KEY,
    media_id INTEGER UNIQUE NOT NULL,
    file_size INTEGER,
    resolution VARCHAR(20),
    duration INTEGER,
    format VARCHAR(16),
    original_filename TEXT,
    mime_type VARCHAR(100),
    -- Nullable solo para objetos anteriores a P04. Las subidas nuevas siempre
    -- lo escriben y el CLI reanudable completa el legado de forma explícita.
    sha256 CHAR(64) NULL,

    -- Vista previa WebP generada en local al subir una foto. Nullable: los
    -- videos, las fotos anteriores a la fase 13 y aquellas cuya generacion
    -- fallo no la tienen, y la entrega cae al original.
    preview_storage_path TEXT NULL,
    preview_mime_type VARCHAR(100) NULL,
    preview_width INTEGER NULL,
    preview_height INTEGER NULL,
    preview_file_size INTEGER NULL,

    CONSTRAINT chk_media_metadata_sha256
        CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT fk_metadata_media FOREIGN KEY (media_id)
        REFERENCES assets(id) ON DELETE CASCADE
);

-- No es UNIQUE: el dueño puede confirmar que quiere guardar una copia exacta.
CREATE INDEX idx_media_metadata_sha256
ON media_metadata (sha256)
WHERE sha256 IS NOT NULL;

-- =========================
-- TAGS (GLOBALES)
-- =========================
CREATE TABLE tags (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER NULL,
    owner_id INTEGER NOT NULL,

    -- El vocabulario pertenece al dueno del album, no a la instalacion: dos
    -- cuentas pueden tener "playa" y ninguna ve la de la otra. `created_by`
    -- no sobra: un colaborador con `organize` crea etiquetas DEL dueno.
    CONSTRAINT fk_tags_owner FOREIGN KEY (owner_id)
        REFERENCES users(id) ON DELETE CASCADE,

    CONSTRAINT uq_tags_owner_name UNIQUE (owner_id, name)
);

-- =========================
-- MEDIA_TAGS (N:M)
-- =========================
CREATE TABLE media_tags (
    media_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,

    PRIMARY KEY (media_id, tag_id),

    CONSTRAINT fk_mt_media FOREIGN KEY (media_id)
        REFERENCES assets(id) ON DELETE CASCADE,

    CONSTRAINT fk_mt_tag FOREIGN KEY (tag_id)
        REFERENCES tags(id) ON DELETE CASCADE
);

-- =========================
-- SMART ALBUMS (L11, revisión 0025)
-- =========================
-- Una búsqueda guardada del dueño, no un álbum: no vive en `albums`, no tiene
-- filas en `album_assets` y nunca copia assets. `filters` es el JSON ya
-- validado por la app (las mismas claves que la búsqueda global); los
-- resultados se calculan al abrirlo.
CREATE TABLE smart_albums (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    titulo VARCHAR(100) NOT NULL,
    descripcion TEXT NULL,
    filters JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NULL,
    CONSTRAINT chk_smart_albums_filters_object CHECK (jsonb_typeof(filters) = 'object')
);
CREATE INDEX idx_smart_albums_user ON smart_albums (user_id, created_at DESC, id DESC);

-- =========================
-- ALBUM SHARES
-- =========================
-- El token de un enlace/invitacion NUNCA se guarda en claro (S08): solo su
-- SHA-256 (`hash_token()` de app/security/sessions.py, mismo patron que
-- user_sessions/registration_invites) sirve para RESOLVER un enlace entrante.
-- token_encrypted es una copia APARTE, cifrada de forma reversible (Fernet,
-- app/security/share_token_crypto.py, clave fuera de la base) para que el
-- dueno pueda volver a mostrar su enlace sin regenerarlo -- nunca se usa
-- para buscar nada, solo para descifrar bajo peticion explicita del dueno.
-- password_hash/allow_original_download/show_metadata son exclusivos de un
-- enlace publico -- chk_public_link_only_extras se lo impone a cualquier
-- otro tipo de compartición.
CREATE TABLE album_shares (
    id SERIAL PRIMARY KEY,
    album_id INTEGER NOT NULL,
    shared_by INTEGER NOT NULL,
    shared_with_user_id INTEGER NULL,
    token_hash CHAR(64) UNIQUE,
    token_encrypted TEXT NULL,
    share_type VARCHAR(20) NOT NULL DEFAULT 'account',
    permission VARCHAR(10) DEFAULT 'read',
    capabilities TEXT[] NOT NULL DEFAULT '{}',
    active BOOLEAN DEFAULT TRUE,
    expires_at TIMESTAMP NULL,
    claimed_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    password_hash TEXT NULL,
    allow_original_download BOOLEAN NOT NULL DEFAULT FALSE,
    show_metadata BOOLEAN NOT NULL DEFAULT TRUE,
    -- Ultimo acceso (S08 seguimiento): sin IP ni ubicacion, mismo criterio
    -- que user_sessions.user_agent_summary.
    last_accessed_at TIMESTAMP NULL,
    last_accessed_user_agent VARCHAR(240) NULL,

    CONSTRAINT fk_share_album FOREIGN KEY (album_id)
        REFERENCES albums(id) ON DELETE CASCADE,

    CONSTRAINT fk_share_by FOREIGN KEY (shared_by)
        REFERENCES users(id) ON DELETE CASCADE,

    CONSTRAINT fk_share_user FOREIGN KEY (shared_with_user_id)
        REFERENCES users(id) ON DELETE CASCADE,

    CONSTRAINT chk_permission CHECK (permission IN ('read', 'write')),
    CONSTRAINT chk_share_type CHECK (share_type IN ('account', 'public_link')),
    CONSTRAINT chk_public_link_read_only CHECK (share_type <> 'public_link' OR permission = 'read'),
    CONSTRAINT chk_public_link_only_extras CHECK (
        share_type = 'public_link'
        OR (password_hash IS NULL AND allow_original_download = FALSE AND show_metadata = TRUE)
    )
);

-- Sesion anonima minima para el visitante de un enlace publico con
-- contrasena: se prueba la contrasena una vez, y una cookie (nunca la
-- contrasena en la URL) autoriza las peticiones siguientes. Mismo patron
-- que user_sessions: solo se guarda sha256(token) de la cookie.
CREATE TABLE share_unlocks (
    id SERIAL PRIMARY KEY,
    share_id INTEGER NOT NULL,
    token_hash CHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_share_unlock_share FOREIGN KEY (share_id)
        REFERENCES album_shares(id) ON DELETE CASCADE
);

-- Preferencias de avisos IN-APP (L12, revisión 0026). 1:1 con users; sin fila
-- = las tres categorías encendidas. No hay push ni interruptor maestro.
CREATE TABLE user_notification_preferences (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    notify_album_invites BOOLEAN NOT NULL DEFAULT TRUE,
    notify_share_claimed BOOLEAN NOT NULL DEFAULT TRUE,
    notify_shared_album_uploads BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =========================
-- ACTIVIDAD DEL ÁLBUM Y AVISOS IN-APP (L12, revisión 0026)
-- =========================
-- Los dos los escribe el servidor en la misma transacción que la operación que
-- los causa. La actividad es del álbum (se va con él); un aviso es de su
-- destinatario (se va con su cuenta). Retención: 365 y 90 días
-- (`python -m app.cli purge-expired-activity-notifications`).
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
);
CREATE INDEX idx_album_activity_album ON album_activity (album_id, created_at DESC, id DESC);

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
);
CREATE INDEX idx_notifications_user ON notifications (user_id, created_at DESC, id DESC);
CREATE INDEX idx_notifications_unread ON notifications (user_id) WHERE read_at IS NULL;

-- =========================
-- CÓDIGOS DE RECUPERACIÓN (L14, revisión 0027)
-- =========================
-- Diez por cuenta, de un solo uso, 150 bits cada uno. Solo su SHA-256
-- (`hash_token()`): el texto en claro existe únicamente en la respuesta que
-- los genera. Recuperar la cuenta borra todos los de esa cuenta.
CREATE TABLE recovery_codes (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash CHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    used_at TIMESTAMP NULL,
    CONSTRAINT chk_recovery_codes_hash CHECK (code_hash ~ '^[0-9a-f]{64}$')
);
CREATE INDEX idx_recovery_codes_user ON recovery_codes (user_id) WHERE used_at IS NULL;

-- PASSKEYS (L15): solo datos publicos; la clave privada nunca sale del dispositivo.
CREATE TABLE webauthn_credentials (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    credential_id BYTEA NOT NULL UNIQUE,
    public_key BYTEA NOT NULL,
    sign_count BIGINT NOT NULL DEFAULT 0,
    transports TEXT[] NOT NULL DEFAULT '{}',
    nickname VARCHAR(60) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP NULL,
    CONSTRAINT chk_webauthn_sign_count CHECK (sign_count >= 0),
    CONSTRAINT chk_webauthn_nickname CHECK (length(btrim(nickname)) > 0)
);
CREATE INDEX idx_webauthn_credentials_user ON webauthn_credentials (user_id);

-- COMENTARIOS (F04): del ASSET, no de un album; texto plano, nunca HTML.
CREATE TABLE asset_comments (
    id BIGSERIAL PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NULL,
    CONSTRAINT chk_asset_comments_body CHECK (
        char_length(body) BETWEEN 1 AND 2000 AND body = btrim(body, E' \t\r\n'))
);
CREATE INDEX idx_asset_comments_asset ON asset_comments (asset_id, created_at, id);
CREATE INDEX idx_asset_comments_user ON asset_comments (user_id);

-- =========================
-- LIMITES DE ABUSO Y REGISTRO POR INVITACION (S03)
-- =========================
-- Contadores de ventana fija para login/registro. Clave compuesta, no un id
-- autoincremental: el UPDATE atomico de try_consume() apunta a la fila
-- exacta sin una consulta previa.
CREATE TABLE rate_limit_counters (
    scope VARCHAR(40) NOT NULL,
    identifier VARCHAR(255) NOT NULL,
    window_key VARCHAR(20) NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    window_ends_at TIMESTAMP NOT NULL,

    PRIMARY KEY (scope, identifier, window_key)
);

-- Solo el SHA-256 del token, nunca el token, igual que user_sessions. Sin
-- columna de quien lo creo: siempre nace del operador via el endpoint
-- interno, nunca de otro usuario.
CREATE TABLE registration_invites (
    id BIGSERIAL PRIMARY KEY,
    token_hash CHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP NULL,
    used_by_user_id INTEGER NULL,

    CONSTRAINT fk_invite_used_by FOREIGN KEY (used_by_user_id)
        REFERENCES users(id) ON DELETE SET NULL
);

-- =========================
-- REPUTACION DE IP (AbuseIPDB, cacheada) -- S04
-- =========================
-- Solo la llena el job programado `python -m app.cli refresh-ip-reputation`,
-- nunca una peticion HTTP: el free tier de blacklist de AbuseIPDB es de 5
-- peticiones/dia. total_reports y country_code quedan NULL con este
-- proveedor (el endpoint gratuito no los trae) -- mismo criterio que
-- media_context.timezone.
CREATE TABLE security_ip_reputation (
    network_or_ip INET NOT NULL PRIMARY KEY,
    abuse_confidence SMALLINT NOT NULL,
    total_reports INTEGER NULL,
    country_code VARCHAR(2) NULL,
    source VARCHAR(30) NOT NULL,
    fetched_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NOT NULL
);

-- =========================
-- SESIONES (opacas, del lado del servidor)
-- =========================
-- Se guarda SOLO el SHA-256 del token de sesion y del secreto CSRF, nunca su
-- valor: un dump de esta base no deja entrar a nadie. CHAR(64) = un hex de
-- SHA-256 exacto. Fechas sin zona como todo el esquema (ver la revision
-- 0013): la caducidad la compara Postgres contra su propio NOW().
CREATE TABLE user_sessions (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    token_hash CHAR(64) NOT NULL UNIQUE,
    csrf_hash CHAR(64) NOT NULL,
    remember_me BOOLEAN NOT NULL DEFAULT FALSE,
    user_agent_summary VARCHAR(240) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    revoked_at TIMESTAMP NULL,

    CONSTRAINT fk_session_user FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE CASCADE
);

-- Challenges WebAuthn (L15): solo su SHA-256, de un solo uso y de minutos.
-- Los de registro quedan atados a la cuenta Y a la sesion que los pidio.
CREATE TABLE webauthn_challenges (
    id BIGSERIAL PRIMARY KEY,
    challenge_hash CHAR(64) NOT NULL UNIQUE,
    purpose VARCHAR(20) NOT NULL,
    user_id INTEGER NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id BIGINT NULL REFERENCES user_sessions(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    CONSTRAINT chk_webauthn_challenge_hash CHECK (challenge_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_webauthn_challenge_purpose CHECK (purpose IN ('registration', 'authentication')),
    CONSTRAINT chk_webauthn_challenge_binding CHECK (
        (purpose = 'registration') = (user_id IS NOT NULL AND session_id IS NOT NULL))
);
CREATE INDEX idx_webauthn_challenges_expires ON webauthn_challenges (expires_at);

-- El indice de media agrega datos de sus tablas 1:1 y N:M. Estos triggers
-- lo refrescan dentro de la misma transaccion que cambia la fuente.
CREATE OR REPLACE FUNCTION albumfp_refresh_media_search(target_id INTEGER)
RETURNS VOID
LANGUAGE SQL
VOLATILE
AS $$
    UPDATE assets AS m
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
    IF TG_TABLE_NAME = 'assets' THEN
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

CREATE OR REPLACE FUNCTION albumfp_normalize_album_search_trigger()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.search_title := albumfp_search_normalize(NEW.titulo);
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_albums_search_refresh
BEFORE INSERT OR UPDATE OF titulo ON albums
FOR EACH ROW EXECUTE FUNCTION albumfp_normalize_album_search_trigger();

CREATE TRIGGER trg_media_search_refresh
AFTER INSERT OR UPDATE OF title, caption ON assets
FOR EACH ROW EXECUTE FUNCTION albumfp_refresh_media_search_trigger();

CREATE TRIGGER trg_media_metadata_search_refresh AFTER INSERT OR UPDATE OR DELETE ON media_metadata
FOR EACH ROW EXECUTE FUNCTION albumfp_refresh_media_search_trigger();
CREATE TRIGGER trg_media_exif_search_refresh AFTER INSERT OR UPDATE OR DELETE ON media_exif
FOR EACH ROW EXECUTE FUNCTION albumfp_refresh_media_search_trigger();
CREATE TRIGGER trg_media_context_search_refresh AFTER INSERT OR UPDATE OR DELETE ON media_context
FOR EACH ROW EXECUTE FUNCTION albumfp_refresh_media_search_trigger();
CREATE TRIGGER trg_media_ocr_search_refresh AFTER INSERT OR UPDATE OR DELETE ON media_ocr
FOR EACH ROW EXECUTE FUNCTION albumfp_refresh_media_search_trigger();
CREATE TRIGGER trg_media_tags_search_refresh AFTER INSERT OR UPDATE OR DELETE ON media_tags
FOR EACH ROW EXECUTE FUNCTION albumfp_refresh_media_search_trigger();
CREATE TRIGGER trg_tags_search_refresh AFTER UPDATE OF name ON tags
FOR EACH ROW EXECUTE FUNCTION albumfp_refresh_media_search_trigger();

-- =========================
-- ÍNDICES (MUY IMPORTANTES)
-- =========================

-- MEDIA
CREATE INDEX idx_album_assets_asset ON album_assets (asset_id);
CREATE INDEX idx_media_user ON assets(user_id);
CREATE INDEX idx_media_deleted_at ON assets(deleted_at);
CREATE INDEX idx_media_created_at ON assets(created_at);
CREATE INDEX idx_media_library_timeline
ON assets (user_id, (COALESCE(taken_at, created_at)) DESC, id DESC)
WHERE deleted_at IS NULL;

CREATE INDEX idx_media_on_this_day
ON assets (
    user_id,
    (EXTRACT(MONTH FROM taken_at)),
    (EXTRACT(DAY FROM taken_at)),
    (EXTRACT(YEAR FROM taken_at)) DESC,
    taken_at DESC,
    id DESC
)
WHERE deleted_at IS NULL AND taken_at IS NOT NULL;

CREATE INDEX idx_media_archive
ON assets (user_id, (COALESCE(taken_at, created_at)) DESC, id DESC)
WHERE deleted_at IS NULL AND archived_at IS NOT NULL;
CREATE INDEX idx_media_search_vector ON assets USING GIN (search_vector);
CREATE INDEX idx_media_search_place_vector ON assets USING GIN (search_place_vector);
CREATE INDEX idx_media_search_title_prefix ON assets (search_title text_pattern_ops);
CREATE INDEX idx_media_search_owner_taken ON assets(user_id, taken_at DESC)
WHERE deleted_at IS NULL;
CREATE INDEX idx_media_search_owner_type ON assets(user_id, file_type, created_at DESC)
WHERE deleted_at IS NULL;
CREATE INDEX idx_media_search_owner_favorite ON assets(user_id, created_at DESC)
WHERE deleted_at IS NULL AND is_favorite = TRUE;

-- Parcial: las fotos sin GPS no ocupan sitio en el indice.
CREATE INDEX idx_media_exif_coords ON media_exif(latitude, longitude)
WHERE latitude IS NOT NULL AND longitude IS NOT NULL;

-- ALBUM SHARES
CREATE INDEX idx_album_shares_album ON album_shares(album_id);
CREATE INDEX idx_album_shares_user ON album_shares(shared_with_user_id);
CREATE INDEX idx_album_shares_user_active ON album_shares(shared_with_user_id, active, expires_at);
CREATE INDEX idx_album_shares_album_active ON album_shares(album_id, active);
CREATE UNIQUE INDEX uq_album_shares_active_user ON album_shares(album_id, shared_with_user_id)
    WHERE active = TRUE AND shared_with_user_id IS NOT NULL;

-- ENLACES CON CONTRASEÑA (sesion anonima)
CREATE INDEX idx_share_unlocks_share ON share_unlocks(share_id);
CREATE INDEX idx_share_unlocks_expires ON share_unlocks(expires_at);

-- MEDIA TAGS
CREATE INDEX idx_tags_owner ON tags(owner_id);
CREATE INDEX idx_media_tags_media ON media_tags(media_id);
CREATE INDEX idx_media_tags_tag ON media_tags(tag_id);

-- ALBUMS (feed público del inicio)
CREATE INDEX idx_albums_public_created ON albums(created_at DESC)
    WHERE is_private = FALSE AND active = TRUE;
CREATE INDEX idx_albums_search_vector ON albums USING GIN (search_vector);
CREATE INDEX idx_albums_search_title_prefix ON albums (search_title text_pattern_ops);

-- SESIONES
-- Parcial a proposito: las sesiones cerradas se conservan como registro pero
-- ninguna consulta las busca, asi que no ocupan sitio en el indice.
CREATE INDEX idx_user_sessions_user_active ON user_sessions(user_id, expires_at)
    WHERE revoked_at IS NULL;

-- LIMITES DE ABUSO
CREATE INDEX idx_rate_limit_window_ends ON rate_limit_counters(window_ends_at);
