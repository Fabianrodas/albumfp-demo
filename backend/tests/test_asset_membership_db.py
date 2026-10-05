"""L10A contra PostgreSQL de verdad: migracion, bajada y comportamiento.

Solo corre con `ALBUMFP_L10A_TEMPLATE_DB` apuntando a una base DESECHABLE en
`0023_media_archive` cuyo nombre empiece por `albumfp_disposable_`. Cada clase
clona esa plantilla (`CREATE DATABASE ... TEMPLATE`), siembra sus fixtures en
el esquema 0023, migra con Alembic en un subproceso y borra el clon al
terminar. Nunca abre la base de `.env` ni escribe en la plantilla.

Sin la variable se omite entera: la suite normal no tiene PostgreSQL. La
evidencia de L10A es esta suite corrida contra la base de ensayo.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
TEMPLATE = (os.getenv("ALBUMFP_L10A_TEMPLATE_DB") or "").strip()
DISPOSABLE = re.compile(r"^albumfp_disposable_[a-z0-9_]+$")

OWNER, COLLAB, STRANGER, OTHER = 900001, 900002, 900003, 900004
ALBUM_A, ALBUM_B, ALBUM_P, ALBUM_I, ALBUM_X = 900101, 900102, 900103, 900104, 900105
PUBLIC_TOKEN = "l10a-public-token-" + "7" * 40
SHA_A = "a1" * 32
SHA_B = "b2" * 32

# id -> (owner, album, file_type, title, favorite, taken_at, created_at, created_by, deleted_at, archived_at)
MEDIA = {
    900201: (OWNER, ALBUM_A, "image", "Playa L10A", True, "2024-01-02 10:00", "2026-01-01 09:00", OWNER, None, None),
    900202: (OWNER, ALBUM_A, "image", "Papelera L10A", False, None, "2026-01-02 09:00", OWNER, "2026-09-01 10:00", None),
    900203: (OWNER, ALBUM_A, "image", "Archivada L10A", False, None, "2026-01-03 09:00", OWNER, None, "2026-09-02 10:00"),
    900204: (OWNER, ALBUM_A, "video", "Del colaborador L10A", False, None, "2026-01-04 09:00", COLLAB, None, None),
    900205: (OWNER, ALBUM_B, "image", "Rio L10A", False, None, "2026-01-05 09:00", OWNER, None, None),
    900206: (OWNER, ALBUM_P, "image", "Publica L10A", False, None, "2026-01-06 09:00", OWNER, None, None),
    900207: (OWNER, ALBUM_I, "image", "Inactiva L10A", False, None, "2026-01-07 09:00", OWNER, None, None),
    900208: (OTHER, ALBUM_X, "image", "Ajena L10A", False, None, "2026-01-08 09:00", OTHER, None, None),
}


def storage_key(media_id: int, owner: int, album: int) -> str:
    return f"user_{owner}/album_{album}/{media_id:032x}.jpg"


def preview_key(media_id: int, owner: int, album: int) -> str:
    return f"user_{owner}/album_{album}/{media_id + 10**9:032x}.webp"


def _skip_reason():
    if not TEMPLATE:
        return "sin ALBUMFP_L10A_TEMPLATE_DB (base desechable en 0023): prueba de PostgreSQL real omitida"
    if not DISPOSABLE.fullmatch(TEMPLATE):
        return "ALBUMFP_L10A_TEMPLATE_DB debe llamarse albumfp_disposable_*"
    return None


def demo_base_url(_appdb):
    from dotenv import dotenv_values
    from sqlalchemy.engine import make_url
    from app.db.safety import parse_demo_database_url

    base_url = (os.getenv("TEST_DATABASE_URL") or "").strip()
    if not base_url:
        base_url = (dotenv_values(BACKEND.parent / ".env").get("TEST_DATABASE_URL") or "").strip()
    if not base_url:
        raise RuntimeError("the PostgreSQL fixtures require TEST_DATABASE_URL")
    return make_url(parse_demo_database_url(base_url, purpose="test"))


def demo_database_url(appdb, database: str):
    return demo_base_url(appdb).set(database=database)


def admin_database_engine(appdb):
    """Use Demo's local admin only for disposable database create/drop work."""
    from dotenv import dotenv_values
    from sqlalchemy import create_engine

    settings = dotenv_values(BACKEND.parent / ".env")
    username = (os.getenv("POSTGRES_ADMIN_USER") or settings.get("POSTGRES_ADMIN_USER") or "").strip()
    password = os.getenv("POSTGRES_ADMIN_PASSWORD") or settings.get("POSTGRES_ADMIN_PASSWORD") or ""
    if username != "albumfp_demo_admin" or not password:
        raise RuntimeError("the PostgreSQL fixtures require Demo's generated local admin credentials")
    url = demo_database_url(appdb, "postgres").set(username=username, password=password)
    return create_engine(url, isolation_level="AUTOCOMMIT")


class Scratch:
    """Un clon desechable de la plantilla, con guardas antes de cualquier escritura."""

    def __init__(self, label: str):
        from sqlalchemy import create_engine, text
        from app.db import db as appdb

        if TEMPLATE == demo_base_url(appdb).database:
            raise RuntimeError("la plantilla no puede ser la base de .env")
        self.name = f"albumfp_disposable_l10a_t{os.getpid()}_{label}"
        if not DISPOSABLE.fullmatch(self.name) or self.name == "albumfp_demo_test":
            raise RuntimeError(f"nombre de base no desechable: {self.name}")
        self._text = text
        self._admin = admin_database_engine(appdb)
        with self._admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)'))
            c.execute(text(
                f'CREATE DATABASE "{self.name}" WITH TEMPLATE "{TEMPLATE}" OWNER "albumfp_demo"'
            ))
        self.engine = create_engine(demo_database_url(appdb, self.name))
        with self.engine.connect() as c:
            if c.execute(text("SELECT current_database()")).scalar() != self.name:
                raise RuntimeError("la conexion no apunta al clon desechable")

    def alembic(self, *args):
        scratch_url = self.engine.url.render_as_string(hide_password=False)
        entorno = {
            **os.environ,
            "APP_ENV": "test",
            "ALBUMFP_DEMO_TEST_MODE": "1",
            "ALBUMFP_DEMO_SCRATCH_DATABASE": self.name,
            "ALBUMFP_L10A_TEMPLATE_DB": TEMPLATE,
            "TEST_DATABASE_URL": scratch_url,
        }
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND, env=entorno, capture_output=True, text=True, timeout=600,
        )

    def version(self) -> str:
        return self.scalar("SELECT version_num FROM alembic_version")

    def scalar(self, sql: str, params: dict | None = None):
        with self.engine.connect() as c:
            return c.execute(self._text(sql), params or {}).scalar()

    def rows(self, sql: str, params: dict | None = None):
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(self._text(sql), params or {}).mappings().all()]

    def execute(self, sql: str, params: dict | None = None):
        with self.engine.begin() as c:
            c.execute(self._text(sql), params or {})

    def drop(self):
        self.engine.dispose()
        with self._admin.connect() as c:
            c.execute(self._text(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)'))
        self._admin.dispose()


def seed_0023(scratch: Scratch) -> None:
    """Fixtures representativas escritas con el esquema 0023 (media.album_id)."""
    scratch.execute(
        """
        INSERT INTO users (id, username, full_name, password_hash) VALUES
          (:o, 'l10a_owner', 'L10A Owner', 'x'), (:c, 'l10a_collab', 'L10A Collab', 'x'),
          (:s, 'l10a_stranger', 'L10A Stranger', 'x'), (:x, 'l10a_other', 'L10A Other', 'x');
        INSERT INTO albums (id, user_id, titulo, is_private, active) VALUES
          (:a, :o, 'L10A A', TRUE, TRUE), (:b, :o, 'L10A B', TRUE, TRUE),
          (:p, :o, 'L10A P', FALSE, TRUE), (:i, :o, 'L10A I', TRUE, FALSE),
          (:xa, :x, 'L10A X', TRUE, TRUE);
        """,
        {"o": OWNER, "c": COLLAB, "s": STRANGER, "x": OTHER, "a": ALBUM_A, "b": ALBUM_B,
         "p": ALBUM_P, "i": ALBUM_I, "xa": ALBUM_X},
    )
    for media_id, (owner, album, kind, title, fav, taken, created, by, deleted, archived) in MEDIA.items():
        scratch.execute(
            """
            INSERT INTO media (id, user_id, album_id, storage_path, file_type, title, caption,
                               is_favorite, taken_at, created_at, created_by, deleted_at, archived_at)
            VALUES (:id, :owner, :album, :path, :kind, :title, 'Leyenda ' || :title,
                    :fav, :taken, :created, :by, :deleted, :archived)
            """,
            {"id": media_id, "owner": owner, "album": album, "path": storage_key(media_id, owner, album),
             "kind": kind, "title": title, "fav": fav, "taken": taken, "created": created, "by": by,
             "deleted": deleted, "archived": archived},
        )
        scratch.execute(
            """
            INSERT INTO media_metadata (media_id, file_size, format, original_filename, mime_type, sha256,
                                        preview_storage_path, preview_mime_type)
            VALUES (:id, 100, 'jpg', :name, 'image/jpeg', :sha, :preview, 'image/webp')
            """,
            {"id": media_id, "name": f"l10a_{media_id}.jpg",
             "sha": SHA_A if media_id == 900201 else SHA_B if media_id == 900205 else None,
             "preview": preview_key(media_id, owner, album)},
        )
    scratch.execute(
        """
        INSERT INTO media_exif (media_id, latitude, longitude, camera_make) VALUES (900201, -2.1, -79.9, 'L10A Cam');
        INSERT INTO media_context (media_id, place_display_name, locality, country_code, country_name)
        VALUES (900201, 'Salinas', 'Salinas', 'EC', 'Ecuador');
        INSERT INTO media_ocr (media_id, extracted_text, provider) VALUES (900201, 'Texto L10A', 'ocr.space');
        INSERT INTO tags (id, name, owner_id, created_by) VALUES (900301, 'playa-l10a', :o, :o);
        INSERT INTO media_tags (media_id, tag_id) VALUES (900201, 900301);
        UPDATE albums SET cover_media_id = 900201 WHERE id = :a;
        UPDATE albums SET cover_media_id = 900205 WHERE id = :b;
        UPDATE albums SET cover_media_id = 900206 WHERE id = :p;
        INSERT INTO album_shares (id, album_id, shared_by, share_type, shared_with_user_id, permission, capabilities, active)
        VALUES (900401, :a, :o, 'account', :c, 'write', '{organize}', TRUE);
        INSERT INTO album_shares (id, album_id, shared_by, share_type, token_hash, permission, active)
        VALUES (900402, :p, :o, 'public_link', :token_hash, 'read', TRUE);
        """,
        {"o": OWNER, "c": COLLAB, "a": ALBUM_A, "b": ALBUM_B, "p": ALBUM_P,
         "token_hash": hashlib.sha256(PUBLIC_TOKEN.encode()).hexdigest()},
    )


def snapshot_0023(scratch: Scratch) -> dict:
    tablas = {}
    for tabla in ("media_exif", "media_context", "media_ocr", "media_metadata", "media_tags"):
        tablas[tabla] = scratch.scalar(
            f"SELECT md5(COALESCE(string_agg(t::text, '|' ORDER BY t::text), '')) FROM {tabla} t"
        )
    return {
        "media": {r["id"]: r for r in scratch.rows(
            """SELECT id, user_id, album_id, storage_path, file_type, title, caption, is_favorite,
                      taken_at, created_at, created_by, deleted_at, archived_at FROM media""")},
        "covers": {r["id"]: r["cover_media_id"] for r in scratch.rows("SELECT id, cover_media_id FROM albums")},
        "dependents": tablas,
    }


L10A_REVISION = "0024_asset_album_membership"


def migrated(label: str, target: str = "head"):
    """Clon sembrado en 0023 y migrado a `target`. Devuelve (scratch, snapshot_previo).

    Las pruebas de migración de L10A fijan su propia revisión (0024): desde L11
    head es 0025, y la evidencia de 0024 no debe depender de lo que venga
    después. Las de comportamiento de la app sí usan head, el código actual."""
    scratch = Scratch(label)
    try:
        seed_0023(scratch)
        previo = snapshot_0023(scratch)
        resultado = scratch.alembic("upgrade", target)
        if resultado.returncode != 0:
            raise AssertionError(f"upgrade fallo:\n{resultado.stdout}\n{resultado.stderr}")
        return scratch, previo
    except Exception:
        scratch.drop()
        raise


@unittest.skipIf(_skip_reason(), _skip_reason())
class MigrationBackfillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.previo = migrated("backfill", L10A_REVISION)

    @classmethod
    def tearDownClass(cls):
        cls.scratch.drop()

    def test_head_is_0024_and_media_became_assets(self):
        self.assertEqual("0024_asset_album_membership", self.scratch.version())
        self.assertIsNone(self.scratch.scalar("SELECT to_regclass('public.media')"))
        self.assertEqual("assets", self.scratch.scalar("SELECT to_regclass('public.assets')::text"))
        self.assertEqual(0, self.scratch.scalar(
            "SELECT count(*) FROM information_schema.columns WHERE table_name = 'assets' AND column_name = 'album_id'"))

    def test_every_media_row_is_one_asset_with_the_same_id_and_fields(self):
        despues = {r["id"]: r for r in self.scratch.rows(
            """SELECT id, user_id, storage_path, file_type, title, caption, is_favorite,
                      taken_at, created_at, created_by, deleted_at, archived_at FROM assets""")}
        self.assertEqual(set(self.previo["media"]), set(despues))
        for media_id, antes in self.previo["media"].items():
            esperado = {k: v for k, v in antes.items() if k != "album_id"}
            self.assertEqual(esperado, despues[media_id], media_id)

    def test_every_asset_has_exactly_its_old_album_as_membership(self):
        filas = self.scratch.rows("SELECT album_id, asset_id, owner_id, added_at, position FROM album_assets")
        self.assertEqual(len(self.previo["media"]), len(filas))
        por_asset = {f["asset_id"]: f for f in filas}
        self.assertEqual(set(self.previo["media"]), set(por_asset))
        for media_id, antes in self.previo["media"].items():
            fila = por_asset[media_id]
            self.assertEqual(antes["album_id"], fila["album_id"], media_id)
            self.assertEqual(antes["user_id"], fila["owner_id"], media_id)
            self.assertEqual(antes["created_at"], fila["added_at"], media_id)
            self.assertIsNone(fila["position"])

    def test_dependent_rows_and_covers_are_untouched(self):
        for tabla, huella in self.previo["dependents"].items():
            self.assertEqual(huella, self.scratch.scalar(
                f"SELECT md5(COALESCE(string_agg(t::text, '|' ORDER BY t::text), '')) FROM {tabla} t"), tabla)
        covers = {r["id"]: r["cover_media_id"] for r in self.scratch.rows("SELECT id, cover_media_id FROM albums")}
        self.assertEqual(self.previo["covers"], covers)

    def test_dependent_rows_still_resolve_to_their_asset(self):
        self.assertEqual(900201, self.scratch.scalar(
            "SELECT a.id FROM media_ocr o JOIN assets a ON a.id = o.media_id WHERE o.extracted_text = 'Texto L10A'"))
        self.assertEqual("playa-l10a", self.scratch.scalar(
            "SELECT t.name FROM media_tags mt JOIN tags t ON t.id = mt.tag_id WHERE mt.media_id = 900201"))

    def test_search_index_keeps_refreshing_after_the_rename(self):
        self.scratch.execute("UPDATE assets SET title = 'Zanahoria Única L10A' WHERE id = 900205")
        self.assertEqual("zanahoria unica l10a", self.scratch.scalar(
            "SELECT search_title FROM assets WHERE id = 900205"))
        self.scratch.execute("UPDATE media_ocr SET extracted_text = 'Cartel Naranja' WHERE media_id = 900201")
        self.assertEqual(900201, self.scratch.scalar(
            "SELECT id FROM assets WHERE search_vector @@ to_tsquery('simple', 'naranja')"))

    def test_membership_cannot_cross_owners(self):
        from sqlalchemy.exc import IntegrityError

        with self.assertRaises(IntegrityError):
            self.scratch.execute(
                "INSERT INTO album_assets (album_id, asset_id, owner_id) VALUES (:x, 900201, :o)",
                {"x": ALBUM_X, "o": OWNER})
        with self.assertRaises(IntegrityError):
            self.scratch.execute(
                "INSERT INTO album_assets (album_id, asset_id, owner_id) VALUES (:x, 900201, :other)",
                {"x": ALBUM_X, "other": OTHER})

    def test_cover_must_reference_a_member_and_clears_with_its_membership(self):
        from sqlalchemy.exc import IntegrityError

        with self.assertRaises(IntegrityError):
            self.scratch.execute("UPDATE albums SET cover_media_id = 900206 WHERE id = :b", {"b": ALBUM_B})
        self.scratch.execute("INSERT INTO album_assets (album_id, asset_id, owner_id) VALUES (:b, 900206, :o)",
                             {"b": ALBUM_B, "o": OWNER})
        self.scratch.execute("UPDATE albums SET cover_media_id = 900206 WHERE id = :b", {"b": ALBUM_B})
        self.scratch.execute("DELETE FROM album_assets WHERE album_id = :b AND asset_id = 900206", {"b": ALBUM_B})
        self.assertIsNone(self.scratch.scalar("SELECT cover_media_id FROM albums WHERE id = :b", {"b": ALBUM_B}))
        self.scratch.execute("UPDATE albums SET cover_media_id = 900205 WHERE id = :b", {"b": ALBUM_B})


@unittest.skipIf(_skip_reason(), _skip_reason())
class MigrationDowngradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.previo = migrated("downgrade", L10A_REVISION)

    @classmethod
    def tearDownClass(cls):
        cls.scratch.drop()

    def test_1_downgrade_refuses_when_an_asset_has_two_memberships(self):
        self.scratch.execute("INSERT INTO album_assets (album_id, asset_id, owner_id) VALUES (:b, 900201, :o)",
                             {"b": ALBUM_B, "o": OWNER})
        try:
            resultado = self.scratch.alembic("downgrade", "0023_media_archive")
            self.assertNotEqual(0, resultado.returncode)
            self.assertIn("1 asset(s) en varios álbumes", resultado.stdout + resultado.stderr)
            self.assertEqual("0024_asset_album_membership", self.scratch.version())
            self.assertEqual(len(self.previo["media"]) + 1, self.scratch.scalar("SELECT count(*) FROM album_assets"))
        finally:
            self.scratch.execute("DELETE FROM album_assets WHERE album_id = :b AND asset_id = 900201", {"b": ALBUM_B})

    def test_2_downgrade_refuses_when_an_asset_has_no_album(self):
        self.scratch.execute("UPDATE albums SET cover_media_id = NULL WHERE id = :b", {"b": ALBUM_B})
        self.scratch.execute("DELETE FROM album_assets WHERE asset_id = 900205")
        try:
            resultado = self.scratch.alembic("downgrade", "0023_media_archive")
            self.assertNotEqual(0, resultado.returncode)
            self.assertIn("1 asset(s) sin álbum", resultado.stdout + resultado.stderr)
            self.assertEqual("0024_asset_album_membership", self.scratch.version())
        finally:
            self.scratch.execute(
                "INSERT INTO album_assets (album_id, asset_id, owner_id, added_at) VALUES (:b, 900205, :o, '2026-01-05 09:00')",
                {"b": ALBUM_B, "o": OWNER})
            self.scratch.execute("UPDATE albums SET cover_media_id = 900205 WHERE id = :b", {"b": ALBUM_B})

    def test_3_round_trip_restores_album_id_and_upgrades_again(self):
        bajada = self.scratch.alembic("downgrade", "0023_media_archive")
        self.assertEqual(0, bajada.returncode, bajada.stdout + bajada.stderr)
        self.assertEqual("0023_media_archive", self.scratch.version())
        restaurado = {r["id"]: r for r in self.scratch.rows(
            """SELECT id, user_id, album_id, storage_path, file_type, title, caption, is_favorite,
                      taken_at, created_at, created_by, deleted_at, archived_at FROM media""")}
        self.assertEqual(self.previo["media"], restaurado)
        self.assertIsNone(self.scratch.scalar("SELECT to_regclass('public.album_assets')"))
        self.scratch.execute("UPDATE media SET title = 'Vuelta Atrás' WHERE id = 900205")
        self.assertEqual("vuelta atras", self.scratch.scalar("SELECT search_title FROM media WHERE id = 900205"))

        subida = self.scratch.alembic("upgrade", L10A_REVISION)
        self.assertEqual(0, subida.returncode, subida.stdout + subida.stderr)
        self.assertEqual(len(self.previo["media"]), self.scratch.scalar("SELECT count(*) FROM album_assets"))


class _AppCase(unittest.TestCase):
    """Clon migrado + app Flask real con sesiones reales y archivos reales."""

    label = "app"

    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.previo = migrated(cls.label)
        try:
            cls.scratch.execute(
                "INSERT INTO album_assets (album_id, asset_id, owner_id, added_at) VALUES (:b, 900201, :o, '2026-02-01')",
                {"b": ALBUM_B, "o": OWNER})
            cls._tmp = tempfile.mkdtemp(prefix="albumfp-l10a-")
            raiz = Path(cls._tmp)
            cls.media_root = raiz / "media"
            for media_id, (owner, album, *_resto) in MEDIA.items():
                cls.write_object(storage_key(media_id, owner, album))
                cls.write_object(preview_key(media_id, owner, album))
            cls._env = patch.dict(os.environ, {
                "MEDIA_STORAGE_BACKEND": "local",
                "MEDIA_STORAGE_ROOT": str(cls.media_root),
                "MEDIA_QUARANTINE_ROOT": str(raiz / "quarantine"),
                "MEDIA_WORK_ROOT": str(raiz / "work"),
                "MEDIA_STATE_ROOT": str(raiz / "state"),
                "USE_X_ACCEL_REDIRECT": "false",
                "APP_ENV": "development",
                "HTTPS_ENABLED": "false",
            })
            cls._env.start()
            from app.db import db as appdb
            from app.storage import backends, media_storage

            cls._engine_patch = patch.object(appdb, "engine", cls.scratch.engine)
            cls._engine_patch.start()
            cls._root_before = media_storage._STORAGE_ROOT
            media_storage._STORAGE_ROOT = cls.media_root
            backends.reset_storage_backend()
            from application import create_app

            cls.app = create_app()
            cls.sessions = {}
            from app.security.sessions import create_session

            with cls.scratch.engine.begin() as conn:
                for user in (OWNER, COLLAB, STRANGER, OTHER):
                    cls.sessions[user] = create_session(conn, user, False, "l10a-test")
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        from app.storage import backends, media_storage

        for nombre in ("_engine_patch", "_env"):
            parche = cls.__dict__.get(nombre)
            if parche:
                parche.stop()
        if "_root_before" in cls.__dict__:
            media_storage._STORAGE_ROOT = cls._root_before
        backends.reset_storage_backend()
        if cls.__dict__.get("_tmp"):
            shutil.rmtree(cls._tmp, ignore_errors=True)
        cls.scratch.drop()

    @classmethod
    def write_object(cls, key: str) -> Path:
        destino = cls.media_root / key
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"\xff\xd8\xff" + key.encode())
        return destino

    def call(self, user: int, method: str, path: str, **kwargs):
        from app.security.sessions import session_cookie_name

        cliente = self.app.test_client()
        sesion = self.sessions[user]
        cliente.set_cookie(session_cookie_name(), sesion["session_token"])
        cabeceras = {"X-CSRF-Token": sesion["csrf_token"]}
        cabeceras.update(kwargs.pop("headers", {}))
        return getattr(cliente, method)(path, headers=cabeceras, **kwargs)

    def ids(self, response) -> list[int]:
        self.assertEqual(200, response.status_code, response.get_json())
        return [item["id"] for item in response.get_json()["data"]]


@unittest.skipIf(_skip_reason(), _skip_reason())
class MembershipAccessTests(_AppCase):
    label = "access"

    def test_library_lists_a_multi_album_asset_once(self):
        ids = self.ids(self.call(OWNER, "get", "/api/media/library"))
        self.assertEqual(1, ids.count(900201))
        self.assertEqual({900201, 900204, 900205, 900206}, set(ids))

    def test_search_favorites_and_home_list_it_once(self):
        self.assertEqual([900201], self.ids(self.call(OWNER, "get", "/api/media/search?q=playa")))
        self.assertEqual([900201], self.ids(self.call(OWNER, "get", "/api/media/favorites")))
        recientes = self.ids(self.call(OWNER, "get", "/api/media/library/recent"))
        self.assertEqual(1, recientes.count(900201))

    def test_album_listing_and_album_filter_use_membership(self):
        respuesta = self.call(OWNER, "get", f"/api/albums/{ALBUM_B}/media")
        self.assertEqual({900201, 900205}, set(self.ids(respuesta)))
        self.assertEqual({ALBUM_B}, {m["album_id"] for m in respuesta.get_json()["data"]})
        self.assertEqual({900201, 900205}, set(self.ids(
            self.call(OWNER, "get", f"/api/media/search?album_id={ALBUM_B}"))))

    def test_public_share_exposes_only_members_of_its_album(self):
        base = f"/api/shared/{PUBLIC_TOKEN}"
        listado = self.app.test_client().get(f"{base}/media")
        self.assertEqual([900206], [m["id"] for m in listado.get_json()["data"]["media"]])
        self.assertEqual(404, self.app.test_client().get(f"{base}/media/900205/preview").status_code)
        self.assertEqual(404, self.app.test_client().get(f"{base}/media/900205").status_code)
        publica = self.app.test_client().get(f"{base}/media/900206/preview")
        self.assertEqual(200, publica.status_code)
        publica.close()

    def test_public_share_follows_membership_changes(self):
        base = f"/api/shared/{PUBLIC_TOKEN}/media/900205/preview"
        self.scratch.execute("INSERT INTO album_assets (album_id, asset_id, owner_id) VALUES (:p, 900205, :o)",
                             {"p": ALBUM_P, "o": OWNER})
        try:
            respuesta = self.app.test_client().get(base)
            self.assertEqual(200, respuesta.status_code)
            respuesta.close()
        finally:
            self.scratch.execute("DELETE FROM album_assets WHERE album_id = :p AND asset_id = 900205", {"p": ALBUM_P})
        self.assertEqual(404, self.app.test_client().get(base).status_code)

    def test_collaborator_reads_only_through_the_album_that_grants_it(self):
        self.assertEqual(200, self.call(COLLAB, "get", "/api/media/900201").status_code)
        for suffix in ("", "/file", "/preview", "/context", "/ocr"):
            with self.subTest(suffix=suffix):
                self.assertEqual(404, self.call(COLLAB, "get", f"/api/media/900205{suffix}").status_code)

    def test_collaborator_capabilities_come_from_the_granting_album(self):
        self.assertEqual(200, self.call(COLLAB, "patch", "/api/media/900201/favorite",
                                        json={"is_favorite": True}).status_code)
        self.assertEqual(403, self.call(COLLAB, "delete", "/api/media/900201").status_code)
        self.assertEqual(404, self.call(COLLAB, "patch", "/api/media/900205/favorite",
                                        json={"is_favorite": True}).status_code)
        self.assertIsNone(self.scratch.scalar("SELECT deleted_at FROM assets WHERE id = 900201"))

    def test_inaccessible_media_routes_hide_asset_existence_including_range(self):
        suffixes = ("", "/file", "/preview", "/context", "/ocr")
        for suffix in suffixes:
            with self.subTest(suffix=suffix):
                existing = self.call(STRANGER, "get", f"/api/media/900201{suffix}")
                missing = self.call(STRANGER, "get", f"/api/media/999999{suffix}")
                self.assertEqual(404, existing.status_code)
                self.assertEqual(missing.status_code, existing.status_code)
                self.assertEqual("application/json", existing.mimetype)
                self.assertEqual("application/json", missing.mimetype)

        ranged = self.call(
            STRANGER, "get", "/api/media/900201/file", headers={"Range": "bytes=0-2"}
        )
        self.assertEqual(404, ranged.status_code)
        self.assertEqual("application/json", ranged.mimetype)

    def test_revoked_collaborator_gets_the_same_not_found_as_a_missing_asset(self):
        self.scratch.execute("DELETE FROM album_shares WHERE id = 900401")
        try:
            for suffix in ("", "/file", "/preview", "/context", "/ocr"):
                with self.subTest(suffix=suffix):
                    existing = self.call(COLLAB, "get", f"/api/media/900201{suffix}")
                    missing = self.call(COLLAB, "get", f"/api/media/999999{suffix}")
                    self.assertEqual(404, existing.status_code)
                    self.assertEqual(missing.status_code, existing.status_code)
                    self.assertEqual("application/json", existing.mimetype)
        finally:
            self.scratch.execute(
                "INSERT INTO album_shares (id, album_id, shared_by, share_type, shared_with_user_id, permission, capabilities, active) "
                "VALUES (900401, :a, :o, 'account', :c, 'write', '{organize}', TRUE) "
                "ON CONFLICT (id) DO NOTHING",
                {"a": ALBUM_A, "o": OWNER, "c": COLLAB},
            )

    def test_context_enrichment_hides_ids_from_non_viewers_but_keeps_capability_403(self):
        for action in ("location", "solar", "holiday"):
            path = f"/api/media/900201/context/{action}"
            missing = f"/api/media/999999/context/{action}"
            with self.subTest(action=action):
                self.assertEqual(404, self.call(STRANGER, "post", path, json={}).status_code)
                self.assertEqual(404, self.call(STRANGER, "post", missing, json={}).status_code)
                self.assertEqual(403, self.call(COLLAB, "post", path, json={}).status_code)

    def test_media_mutations_hide_existing_assets_from_non_viewers(self):
        routes = (
            ("patch", "/api/media/900201", {"caption": "probe"}),
            ("delete", "/api/media/900201", {}),
            ("patch", "/api/media/900201/favorite", {"is_favorite": True}),
            ("patch", "/api/media/900201/archive", {"archived": True}),
            ("post", "/api/media/900201/context/enrich", {}),
            ("post", "/api/media/900201/ocr", {}),
            ("post", "/api/media/900201/tag-suggestions", {}),
            ("delete", "/api/media/900201/ocr", {}),
        )
        for method, path, body in routes:
            missing = path.replace("900201", "999999")
            with self.subTest(method=method, path=path):
                self.assertEqual(404, self.call(STRANGER, method, path, json=body).status_code)
                self.assertEqual(404, self.call(STRANGER, method, missing, json=body).status_code)
        for method, path, body in (
            ("patch", "/api/media/900201", {"caption": "probe"}),
            ("delete", "/api/media/900201", {}),
            ("post", "/api/media/900201/ocr", {}),
            ("post", "/api/media/900201/tag-suggestions", {}),
            ("delete", "/api/media/900201/ocr", {}),
        ):
            with self.subTest(readable_collaborator=True, method=method, path=path):
                self.assertEqual(403, self.call(COLLAB, method, path, json=body).status_code)

    def test_restore_hides_another_users_trashed_asset(self):
        self.assertEqual(404, self.call(STRANGER, "post", "/api/media/900202/restore").status_code)
        self.assertEqual(404, self.call(STRANGER, "post", "/api/media/999999/restore").status_code)

    def test_strangers_read_only_public_assets(self):
        self.assertEqual(200, self.call(STRANGER, "get", "/api/media/900206").status_code)

    def test_owner_detail_reports_the_context_album(self):
        respuesta = self.call(OWNER, "get", "/api/media/900205")
        self.assertEqual(200, respuesta.status_code)
        self.assertEqual(ALBUM_B, respuesta.get_json()["data"]["media"]["album_id"])
        self.assertEqual("owner", respuesta.get_json()["data"]["album_role"])

    def test_cover_update_requires_membership(self):
        self.assertEqual(400, self.call(OWNER, "patch", f"/api/albums/{ALBUM_B}",
                                        json={"cover_media_id": 900206}).status_code)
        self.assertEqual(200, self.call(OWNER, "patch", f"/api/albums/{ALBUM_B}",
                                        json={"cover_media_id": 900201}).status_code)
        self.assertEqual(900201, self.scratch.scalar("SELECT cover_media_id FROM albums WHERE id = :b", {"b": ALBUM_B}))
        self.scratch.execute("UPDATE albums SET cover_media_id = 900205 WHERE id = :b", {"b": ALBUM_B})

    def test_duplicate_lookup_keeps_collaborators_inside_the_target_album(self):
        from app.media.checksums import find_exact_duplicate

        with self.scratch.engine.begin() as conn:
            self.assertIsNone(find_exact_duplicate(
                conn, owner_id=OWNER, requester_id=COLLAB, target_album_id=ALBUM_A, sha256=SHA_B))
            self.assertEqual({"id": 900205, "album_id": ALBUM_B}, find_exact_duplicate(
                conn, owner_id=OWNER, requester_id=OWNER, target_album_id=ALBUM_A, sha256=SHA_B))
            self.assertEqual({"id": 900201, "album_id": ALBUM_A}, find_exact_duplicate(
                conn, owner_id=OWNER, requester_id=COLLAB, target_album_id=ALBUM_A, sha256=SHA_A))


@unittest.skipIf(_skip_reason(), _skip_reason())
class MembershipDeletionTests(_AppCase):
    label = "deletion"

    def exists(self, media_id: int) -> bool:
        return bool(self.scratch.scalar("SELECT count(*) FROM assets WHERE id = :id", {"id": media_id}))

    def memberships(self, media_id: int) -> set[int]:
        return {r["album_id"] for r in self.scratch.rows(
            "SELECT album_id FROM album_assets WHERE asset_id = :id", {"id": media_id})}

    def unassigned_trashed(self, media_id: int, deleted_at: str = "NOW()") -> Path:
        self.scratch.execute(
            f"""INSERT INTO assets (id, user_id, storage_path, file_type, title, deleted_at)
                VALUES (:id, :o, :path, 'image', 'Suelta L10A', {deleted_at})""",
            {"id": media_id, "o": OWNER, "path": storage_key(media_id, OWNER, ALBUM_A)})
        return self.write_object(storage_key(media_id, OWNER, ALBUM_A))

    def test_deleting_an_album_keeps_assets_other_memberships_and_files(self):
        self.assertEqual(200, self.call(OWNER, "delete", f"/api/albums/{ALBUM_A}").status_code)
        self.assertIsNone(self.scratch.scalar("SELECT id FROM albums WHERE id = :a", {"a": ALBUM_A}))
        for media_id in (900201, 900202, 900203, 900204):
            self.assertTrue(self.exists(media_id), media_id)
            self.assertTrue((self.media_root / storage_key(media_id, OWNER, ALBUM_A)).exists(), media_id)
        self.assertEqual({ALBUM_B}, self.memberships(900201))
        self.assertEqual(set(), self.memberships(900204))
        self.assertEqual({ALBUM_B}, self.memberships(900205))
        self.assertEqual({ALBUM_P}, self.memberships(900206))

        biblioteca = self.ids(self.call(OWNER, "get", "/api/media/library"))
        self.assertIn(900204, biblioteca)
        self.assertEqual(1, biblioteca.count(900201))
        self.assertIn(900203, self.ids(self.call(OWNER, "get", "/api/media/library?archived=only")))
        self.assertIn(900202, self.ids(self.call(OWNER, "get", "/api/trash")))
        detalle = self.call(OWNER, "get", "/api/media/900204")
        self.assertEqual(200, detalle.status_code)
        self.assertIsNone(detalle.get_json()["data"]["media"]["album_id"])
        self.assertEqual(404, self.call(COLLAB, "get", "/api/media/900204").status_code)
        self.assertEqual(404, self.call(STRANGER, "get", "/api/media/900204").status_code)

    def test_permanent_delete_of_an_unassigned_asset_removes_its_files(self):
        archivo = self.unassigned_trashed(900209)
        self.assertEqual(200, self.call(OWNER, "delete", "/api/media/900209/permanent").status_code)
        self.assertFalse(self.exists(900209))
        self.assertFalse(archivo.exists())

    def test_empty_trash_includes_unassigned_assets(self):
        archivo = self.unassigned_trashed(900210)
        self.assertEqual(200, self.call(OWNER, "delete", "/api/trash").status_code)
        self.assertFalse(self.exists(900210))
        self.assertFalse(archivo.exists())

    def test_expired_trash_purge_includes_unassigned_assets(self):
        from app import cli

        archivo = self.unassigned_trashed(900211, "NOW() - INTERVAL '40 days'")
        self.assertEqual(0, cli.purge_expired_trash())
        self.assertFalse(self.exists(900211))
        self.assertFalse(archivo.exists())

    def test_account_deletion_cascades_through_memberships_covers_and_files(self):
        """Borrar la cuenta recorre users -> albums/assets -> album_assets, con la
        portada compuesta (fk_album_cover_member) en medio del ciclo."""
        self.scratch.execute("UPDATE albums SET cover_media_id = 900208 WHERE id = :x", {"x": ALBUM_X})
        original = self.media_root / storage_key(900208, OTHER, ALBUM_X)
        self.assertEqual(200, self.call(OTHER, "delete", "/auth/me", json={"username": "l10a_other"}).status_code)
        self.assertIsNone(self.scratch.scalar("SELECT id FROM albums WHERE id = :x", {"x": ALBUM_X}))
        self.assertFalse(self.exists(900208))
        self.assertEqual(0, self.scratch.scalar("SELECT count(*) FROM album_assets WHERE owner_id = :o", {"o": OTHER}))
        self.assertFalse(original.exists())
        self.assertTrue(self.exists(900205))

    def test_trash_is_global_and_restore_keeps_every_membership(self):
        self.scratch.execute("INSERT INTO album_assets (album_id, asset_id, owner_id) VALUES (:b, 900206, :o)",
                             {"b": ALBUM_B, "o": OWNER})
        self.assertEqual(200, self.call(OWNER, "delete", "/api/media/900206").status_code)
        for album in (ALBUM_B, ALBUM_P):
            self.assertNotIn(900206, self.ids(self.call(OWNER, "get", f"/api/albums/{album}/media")), album)
        self.assertEqual({ALBUM_B, ALBUM_P}, self.memberships(900206))
        self.assertEqual(200, self.call(OWNER, "post", "/api/media/900206/restore").status_code)
        for album in (ALBUM_B, ALBUM_P):
            self.assertIn(900206, self.ids(self.call(OWNER, "get", f"/api/albums/{album}/media")), album)


@unittest.skipIf(_skip_reason(), _skip_reason())
class SchemaSnapshotParityTests(unittest.TestCase):
    """`schema.sql` (instalacion desde cero) y la cadena de migraciones deben
    producir los mismos objetos de L10A."""

    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine, text
        from app.db import db as appdb

        cls.scratch, _ = migrated("parity")
        cls.fresh_name = f"albumfp_disposable_l10a_t{os.getpid()}_fresh"
        cls._admin = admin_database_engine(appdb)
        with cls._admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{cls.fresh_name}" WITH (FORCE)'))
            c.execute(text(
                f'CREATE DATABASE "{cls.fresh_name}" WITH TEMPLATE template0 OWNER "albumfp_demo"'
            ))
        cls.fresh = create_engine(demo_database_url(appdb, cls.fresh_name))
        sql = (BACKEND / "schemas" / "schema.sql").read_text(encoding="utf-8")
        with cls.fresh.begin() as c:
            c.exec_driver_sql(sql)

    @classmethod
    def tearDownClass(cls):
        from sqlalchemy import text

        cls.fresh.dispose()
        with cls._admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{cls.fresh_name}" WITH (FORCE)'))
        cls._admin.dispose()
        cls.scratch.drop()

    CATALOG = {
        "columns": """SELECT table_name, column_name, data_type, is_nullable, COALESCE(column_default, '')
                      FROM information_schema.columns
                      WHERE table_schema = 'public' AND table_name IN ('assets', 'album_assets')""",
        # Claves primarias, unicas y foraneas: lo que L10A cambia. Los CHECK
        # anteriores y los NOT NULL que PostgreSQL 18 cataloga con el nombre
        # viejo de la tabla no son de esta fase (la nulabilidad ya se compara
        # en "columns").
        "constraints": """SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)
                          FROM pg_constraint
                          WHERE contype IN ('p', 'u', 'f')
                            AND (conrelid IN ('assets'::regclass, 'album_assets'::regclass)
                                 OR (conrelid = 'albums'::regclass
                                     AND conname IN ('fk_cover_media', 'uq_albums_id_owner',
                                                     'fk_album_cover_member')))""",
        "indexes": """SELECT tablename, indexname, indexdef FROM pg_indexes
                      WHERE schemaname = 'public' AND tablename IN ('assets', 'album_assets')""",
        "triggers": """SELECT tgrelid::regclass::text, tgname, pg_get_triggerdef(oid)
                       FROM pg_trigger WHERE NOT tgisinternal AND tgrelid = 'assets'::regclass""",
        "functions": """SELECT proname, regexp_replace(prosrc, '\\s+', ' ', 'g') FROM pg_proc
                        WHERE proname IN ('albumfp_refresh_media_search', 'albumfp_refresh_media_search_trigger')""",
    }

    def _catalog(self, engine, sql):
        from sqlalchemy import text

        with engine.connect() as c:
            return sorted(tuple(str(v).strip() for v in row) for row in c.execute(text(sql)).all())

    def test_l10a_objects_match_between_schema_sql_and_migrations(self):
        for nombre, sql in self.CATALOG.items():
            with self.subTest(objeto=nombre):
                self.assertEqual(self._catalog(self.scratch.engine, sql), self._catalog(self.fresh, sql))


def small_jpeg(color: tuple[int, int, int]) -> bytes:
    import io

    from PIL import Image

    salida = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(salida, format="JPEG")
    return salida.getvalue()


@unittest.skipIf(_skip_reason(), _skip_reason())
class MembershipWorkflowTests(_AppCase):
    """L10B: el dueño añade y quita pertenencias; nunca se copia, mueve ni
    borra un objeto, y nadie más puede tocarlas."""

    label = "workflow"

    def memberships(self, media_id: int) -> set[int]:
        return {r["album_id"] for r in self.scratch.rows(
            "SELECT album_id FROM album_assets WHERE asset_id = :id", {"id": media_id})}

    def all_memberships(self) -> list[tuple[int, int]]:
        return sorted((r["album_id"], r["asset_id"]) for r in self.scratch.rows(
            "SELECT album_id, asset_id FROM album_assets"))

    def asset_count(self) -> int:
        return self.scratch.scalar("SELECT count(*) FROM assets")

    def object_files(self) -> list[str]:
        return sorted(p.relative_to(self.media_root).as_posix() for p in self.media_root.rglob("*") if p.is_file())

    def put_member(self, user: int, album: int, asset: int):
        return self.call(user, "put", f"/api/albums/{album}/assets/{asset}")

    def delete_member(self, user: int, album: int, asset: int):
        return self.call(user, "delete", f"/api/albums/{album}/assets/{asset}")

    def upload(self, album: int, contenido: bytes):
        import io

        return self.call(OWNER, "post", f"/api/albums/{album}/media",
                         data={"file": (io.BytesIO(contenido), "l10b.jpg", "image/jpeg")},
                         content_type="multipart/form-data")

    def test_owner_adds_an_asset_to_another_album_without_new_bytes(self):
        antes = (self.asset_count(), self.object_files(),
                 self.scratch.scalar("SELECT storage_path FROM assets WHERE id = 900204"))
        try:
            respuesta = self.put_member(OWNER, ALBUM_B, 900204)
            self.assertEqual(200, respuesta.status_code, respuesta.get_json())
            self.assertEqual({"album_id": ALBUM_B, "asset_id": 900204, "added": True},
                             respuesta.get_json()["data"])
            self.assertEqual({ALBUM_A, ALBUM_B}, self.memberships(900204))
            self.assertEqual(antes, (self.asset_count(), self.object_files(),
                                     self.scratch.scalar("SELECT storage_path FROM assets WHERE id = 900204")))
            self.assertEqual(1, self.ids(self.call(OWNER, "get", f"/api/albums/{ALBUM_B}/media")).count(900204))

            otra_vez = self.put_member(OWNER, ALBUM_B, 900204)
            self.assertEqual(200, otra_vez.status_code)
            self.assertFalse(otra_vez.get_json()["data"]["added"])
            self.assertEqual({ALBUM_A, ALBUM_B}, self.memberships(900204))
        finally:
            self.scratch.execute("DELETE FROM album_assets WHERE album_id = :b AND asset_id = 900204", {"b": ALBUM_B})

    def test_removing_one_membership_keeps_the_asset_and_its_other_albums(self):
        try:
            respuesta = self.delete_member(OWNER, ALBUM_B, 900201)
            self.assertEqual(200, respuesta.status_code, respuesta.get_json())
            self.assertEqual({"album_id": ALBUM_B, "asset_id": 900201, "remaining_albums": 1},
                             respuesta.get_json()["data"])
            self.assertEqual({ALBUM_A}, self.memberships(900201))
            self.assertTrue((self.media_root / storage_key(900201, OWNER, ALBUM_A)).exists())
            self.assertIsNone(self.scratch.scalar("SELECT deleted_at FROM assets WHERE id = 900201"))
            self.assertNotIn(900201, self.ids(self.call(OWNER, "get", f"/api/albums/{ALBUM_B}/media")))
            self.assertIn(900201, self.ids(self.call(OWNER, "get", f"/api/albums/{ALBUM_A}/media")))
        finally:
            self.scratch.execute(
                "INSERT INTO album_assets (album_id, asset_id, owner_id, added_at) VALUES (:b, 900201, :o, '2026-02-01')"
                " ON CONFLICT DO NOTHING", {"b": ALBUM_B, "o": OWNER})

    def test_removing_the_final_membership_leaves_a_usable_unassigned_asset(self):
        archivo = self.media_root / storage_key(900205, OWNER, ALBUM_B)
        try:
            respuesta = self.delete_member(OWNER, ALBUM_B, 900205)
            self.assertEqual(200, respuesta.status_code, respuesta.get_json())
            self.assertEqual(0, respuesta.get_json()["data"]["remaining_albums"])
            self.assertEqual(set(), self.memberships(900205))
            # Era la portada de B: la pertenencia se la lleva consigo.
            self.assertIsNone(self.scratch.scalar("SELECT cover_media_id FROM albums WHERE id = :b", {"b": ALBUM_B}))
            self.assertTrue(archivo.exists())

            self.assertEqual(1, self.ids(self.call(OWNER, "get", "/api/media/library")).count(900205))
            detalle = self.call(OWNER, "get", "/api/media/900205").get_json()["data"]
            self.assertIsNone(detalle["media"]["album_id"])
            self.assertEqual([], detalle["albums"])
            self.assertEqual("owner", detalle["album_role"])
            vista = self.call(OWNER, "get", "/api/media/900205/preview")
            self.assertEqual(200, vista.status_code)
            vista.close()

            # Tags, texto y contexto siguen funcionando sin album de contexto.
            self.assertEqual(200, self.call(OWNER, "post", "/api/media/900205/tags",
                                            json={"tag_ids": [900301]}).status_code)
            self.assertIn(900301, [t["id"] for t in self.call(OWNER, "get", "/api/tags").get_json()["data"]])
            self.assertEqual(200, self.call(OWNER, "delete", "/api/media/900205/tags/900301").status_code)
            self.assertEqual(200, self.call(OWNER, "get", "/api/media/900205/ocr").status_code)
            self.assertEqual(200, self.call(OWNER, "get", "/api/media/900205/context").status_code)

            # Favorito, archivo, edicion y papelera son globales al asset.
            self.assertEqual(200, self.call(OWNER, "patch", "/api/media/900205/favorite",
                                            json={"is_favorite": True}).status_code)
            self.assertIn(900205, self.ids(self.call(OWNER, "get", "/api/media/favorites")))
            self.assertEqual(200, self.call(OWNER, "patch", "/api/media/900205/favorite",
                                            json={"is_favorite": False}).status_code)
            self.assertEqual(200, self.call(OWNER, "patch", "/api/media/900205/archive",
                                            json={"archived": True}).status_code)
            self.assertIn(900205, self.ids(self.call(OWNER, "get", "/api/media/library?archived=only")))
            self.assertEqual(200, self.call(OWNER, "patch", "/api/media/900205/archive",
                                            json={"archived": False}).status_code)
            self.assertEqual(200, self.call(OWNER, "patch", "/api/media/900205",
                                            json={"title": "Rio suelto L10B"}).status_code)
            self.assertEqual(200, self.call(OWNER, "delete", "/api/media/900205").status_code)
            papelera = self.call(OWNER, "get", "/api/trash").get_json()["data"]
            self.assertEqual([None], [m["album_id"] for m in papelera if m["id"] == 900205])
            self.assertEqual(200, self.call(OWNER, "post", "/api/media/900205/restore").status_code)
            self.assertIn(900205, self.ids(self.call(OWNER, "get", "/api/media/library")))

            # Y vuelve a un album sin subir nada.
            self.assertEqual(200, self.put_member(OWNER, ALBUM_B, 900205).status_code)
            self.assertEqual({ALBUM_B}, self.memberships(900205))
        finally:
            self.scratch.execute(
                "INSERT INTO album_assets (album_id, asset_id, owner_id) VALUES (:b, 900205, :o) ON CONFLICT DO NOTHING",
                {"b": ALBUM_B, "o": OWNER})
            self.scratch.execute(
                "UPDATE assets SET title = 'Rio L10A', deleted_at = NULL, archived_at = NULL WHERE id = 900205")
            self.scratch.execute("UPDATE albums SET cover_media_id = 900205 WHERE id = :b", {"b": ALBUM_B})

    def test_collaborators_and_strangers_cannot_manage_memberships(self):
        antes = self.all_memberships()
        self.scratch.execute(
            "UPDATE album_shares SET capabilities = '{upload,edit_media,delete_media,organize,edit_album}' WHERE id = 900401")
        try:
            for usuario in (COLLAB, STRANGER):
                with self.subTest(usuario=usuario):
                    self.assertEqual(403, self.put_member(usuario, ALBUM_A, 900205).status_code)
                    self.assertEqual(403, self.delete_member(usuario, ALBUM_A, 900201).status_code)
            self.assertEqual(antes, self.all_memberships())
        finally:
            self.scratch.execute("UPDATE album_shares SET capabilities = '{organize}' WHERE id = 900401")

    def test_memberships_never_cross_owners(self):
        antes = self.all_memberships()
        self.assertEqual(404, self.put_member(OWNER, ALBUM_A, 900208).status_code)
        self.assertEqual(403, self.put_member(OWNER, ALBUM_X, 900201).status_code)
        self.assertEqual(404, self.put_member(OTHER, ALBUM_X, 900201).status_code)
        self.assertEqual(403, self.delete_member(OTHER, ALBUM_A, 900201).status_code)
        self.assertEqual(antes, self.all_memberships())

    def test_trashed_missing_and_non_member_assets_are_404(self):
        antes = self.all_memberships()
        self.assertEqual(404, self.put_member(OWNER, ALBUM_B, 900202).status_code)
        self.assertEqual(404, self.put_member(OWNER, ALBUM_B, 999999).status_code)
        self.assertEqual(404, self.delete_member(OWNER, ALBUM_B, 900206).status_code)
        self.assertEqual(antes, self.all_memberships())

    def test_public_share_follows_the_membership_workflow(self):
        base = f"/api/shared/{PUBLIC_TOKEN}/media"
        self.assertEqual(404, self.app.test_client().get(f"{base}/900205/preview").status_code)
        try:
            self.assertEqual(200, self.put_member(OWNER, ALBUM_P, 900205).status_code)
            listado = self.app.test_client().get(base).get_json()["data"]["media"]
            self.assertIn(900205, [m["id"] for m in listado])
            vista = self.app.test_client().get(f"{base}/900205/preview")
            self.assertEqual(200, vista.status_code)
            vista.close()
            self.assertEqual(200, self.delete_member(OWNER, ALBUM_P, 900205).status_code)
            self.assertEqual(404, self.app.test_client().get(f"{base}/900205/preview").status_code)
            self.assertEqual(404, self.app.test_client().get(f"{base}/900205").status_code)
        finally:
            self.scratch.execute("DELETE FROM album_assets WHERE album_id = :p AND asset_id = 900205", {"p": ALBUM_P})

    def test_detail_album_context_is_validated_through_membership(self):
        dueño = self.call(OWNER, "get", f"/api/media/900201?album_id={ALBUM_B}")
        self.assertEqual(200, dueño.status_code, dueño.get_json())
        datos = dueño.get_json()["data"]
        self.assertEqual(ALBUM_B, datos["media"]["album_id"])
        self.assertEqual("owner", datos["album_role"])
        self.assertEqual([{"id": ALBUM_A, "titulo": "L10A A"}, {"id": ALBUM_B, "titulo": "L10A B"}], datos["albums"])

        self.assertEqual(404, self.call(OWNER, "get", f"/api/media/900201?album_id={ALBUM_P}").status_code)
        self.assertEqual(400, self.call(OWNER, "get", "/api/media/900201?album_id=nope").status_code)

        colaborador = self.call(COLLAB, "get", f"/api/media/900201?album_id={ALBUM_A}")
        self.assertEqual(200, colaborador.status_code)
        datos = colaborador.get_json()["data"]
        self.assertEqual((ALBUM_A, "write", ["organize"]),
                         (datos["media"]["album_id"], datos["album_role"], datos["album_capabilities"]))
        # Las otras pertenencias (albumes privados del dueño) no se revelan.
        self.assertNotIn("albums", datos)
        self.assertEqual(404, self.call(COLLAB, "get", f"/api/media/900201?album_id={ALBUM_B}").status_code)

        visitante = self.call(STRANGER, "get", f"/api/media/900206?album_id={ALBUM_P}")
        self.assertEqual(200, visitante.status_code)
        self.assertEqual("read", visitante.get_json()["data"]["album_role"])

    def test_every_personal_view_lists_a_multi_album_asset_once(self):
        extra = 900106
        self.scratch.execute(
            "INSERT INTO albums (id, user_id, titulo, is_private, active) VALUES (:q, :o, 'L10B Q', FALSE, TRUE)",
            {"q": extra, "o": OWNER})
        self.scratch.execute(
            """INSERT INTO album_assets (album_id, asset_id, owner_id) VALUES
               (:b, 900202, :o), (:b, 900203, :o), (:q, 900206, :o), (:q, 900201, :o)""",
            {"b": ALBUM_B, "q": extra, "o": OWNER})
        try:
            self.assertEqual(1, self.ids(self.call(OWNER, "get", "/api/media/library")).count(900201))
            self.assertEqual(1, self.ids(self.call(OWNER, "get", "/api/media/library?archived=only")).count(900203))
            self.assertEqual(1, self.ids(self.call(OWNER, "get", "/api/trash")).count(900202))
            self.assertEqual(1, self.ids(self.call(OWNER, "get", "/api/media/favorites")).count(900201))
            self.assertEqual(1, self.ids(self.call(OWNER, "get", "/api/media/search?q=playa")).count(900201))
            self.assertEqual(1, self.ids(self.call(OWNER, "get", "/api/media/library/recent")).count(900201))
            lugares = self.call(OWNER, "get", "/api/places").get_json()["data"]
            self.assertEqual([1], [g["media_count"] for g in lugares if g["locality"] == "Salinas"])
            self.assertEqual([900201], self.ids(self.call(
                OWNER, "get", "/api/places/media?country_code=EC&locality=Salinas")))
            publico = self.ids(self.call(STRANGER, "get", "/api/media/public?per_page=100"))
            self.assertEqual(1, publico.count(900206))
            self.assertEqual(1, publico.count(900201))
        finally:
            self.scratch.execute("DELETE FROM album_assets WHERE album_id = :q", {"q": extra})
            self.scratch.execute("DELETE FROM album_assets WHERE album_id = :b AND asset_id IN (900202, 900203)",
                                 {"b": ALBUM_B})
            self.scratch.execute("DELETE FROM albums WHERE id = :q", {"q": extra})

    def test_a_duplicate_upload_reuses_the_existing_asset(self):
        contenido = small_jpeg((12, 140, 77))
        subida = self.upload(ALBUM_A, contenido)
        self.assertEqual(201, subida.status_code, subida.get_json())
        asset_id = subida.get_json()["data"]["id"]
        antes = (self.asset_count(), self.object_files())

        repetida = self.upload(ALBUM_B, contenido)
        self.assertEqual(409, repetida.status_code)
        cuerpo = repetida.get_json()
        self.assertEqual(("exact_duplicate", asset_id, ALBUM_A),
                         (cuerpo["code"], cuerpo["existing_media_id"], cuerpo["existing_album_id"]))
        self.assertEqual(200, self.put_member(OWNER, ALBUM_B, asset_id).status_code)
        self.assertEqual({ALBUM_A, ALBUM_B}, self.memberships(asset_id))
        self.assertEqual(antes, (self.asset_count(), self.object_files()))

        # Un duplicado suelto tambien se puede volver a meter en un album.
        self.assertEqual(200, self.delete_member(OWNER, ALBUM_A, asset_id).status_code)
        self.assertEqual(200, self.delete_member(OWNER, ALBUM_B, asset_id).status_code)
        suelta = self.upload(ALBUM_A, contenido)
        self.assertEqual(409, suelta.status_code)
        self.assertEqual((asset_id, None), (suelta.get_json()["existing_media_id"],
                                            suelta.get_json()["existing_album_id"]))
        self.assertEqual(200, self.put_member(OWNER, ALBUM_A, asset_id).status_code)
        self.assertEqual({ALBUM_A}, self.memberships(asset_id))
        self.assertEqual(antes, (self.asset_count(), self.object_files()))


if __name__ == "__main__":
    unittest.main()
