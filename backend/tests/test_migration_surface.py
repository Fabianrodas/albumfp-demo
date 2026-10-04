import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BASELINE = ROOT / "migrations/versions/0001_current_schema_baseline.py"


class MigrationToolingTests(unittest.TestCase):
    def test_alembic_is_a_declared_dependency(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("alembic", requirements.lower())

    def test_env_reuses_the_application_engine_instead_of_reparsing_the_url(self):
        env = (ROOT / "migrations/env.py").read_text(encoding="utf-8")
        self.assertIn("from app.db.db import engine", env)
        self.assertIn("context.configure(connection=connection", env)
        # Una segunda copia de las credenciales se quedaria desincronizada del .env.
        self.assertNotIn("create_engine", env)
        self.assertNotIn("URL.create", env)

    def test_alembic_ini_stores_no_connection_string(self):
        ini = (ROOT / "alembic.ini").read_text(encoding="utf-8")
        self.assertIn("script_location = migrations", ini)
        self.assertNotIn("sqlalchemy.url", ini)
        self.assertNotIn("postgresql", ini)


class BaselineRevisionTests(unittest.TestCase):
    def test_baseline_never_drops_schema(self):
        text = BASELINE.read_text(encoding="utf-8")
        self.assertNotIn("DROP TABLE", text.upper())
        self.assertNotIn("DROP TYPE", text.upper())
        self.assertIn('revision = "0001_current_schema_baseline"', text)

    def test_baseline_is_a_marker_with_no_schema_operations(self):
        text = BASELINE.read_text(encoding="utf-8")
        self.assertIn("down_revision = None", text)
        # Un marcador no ejecuta nada: si alguien le anade operaciones, sellar
        # una base ya existente empezaria a modificarla.
        self.assertNotIn("op.", text)
        self.assertNotIn("execute(", text)


class DestructiveResetStaysExplicitTests(unittest.TestCase):
    def test_schema_py_remains_the_explicitly_destructive_path(self):
        text = (ROOT / "schemas/schema.py").read_text(encoding="utf-8")
        self.assertIn("DROP TABLE IF EXISTS", text)
        self.assertIn("reset: bool = True", text)

    def test_readme_separates_reset_from_migration(self):
        readme = (ROOT.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("alembic upgrade head", readme)
        self.assertIn("alembic stamp 0001_current_schema_baseline", readme)
        self.assertIn("python -m schemas.schema", readme)


if __name__ == "__main__":
    unittest.main()


class RevisionesPosterioresTests(unittest.TestCase):
    VERSIONES = ROOT / "migrations/versions"

    def test_toda_revision_que_no_sea_la_base_sabe_deshacerse(self):
        for archivo in sorted(self.VERSIONES.glob("[0-9]*.py")):
            if archivo.name.startswith("0001_"):
                continue  # la base es un marcador: no crea nada, no deshace nada
            texto = archivo.read_text(encoding="utf-8")
            with self.subTest(revision=archivo.name):
                cuerpo = texto.split("def downgrade():", 1)
                self.assertEqual(2, len(cuerpo), "falta downgrade")
                self.assertIn("DROP", cuerpo[1].upper(), "un downgrade que no deshace nada no sirve")

    def test_la_migracion_y_schema_sql_describen_la_misma_tabla(self):
        # El trampa documentada en las reglas: una migracion nueva y
        # `schema.sql` se separan y una instalacion desde cero sale distinta.
        migracion = (self.VERSIONES / "0002_media_exif.py").read_text(encoding="utf-8")
        schema = (ROOT / "schemas/schema.sql").read_text(encoding="utf-8")
        for pieza in ("media_exif", "taken_at_original", "latitude", "longitude", "altitude_m",
                      "camera_make", "camera_model", "orientation", "extracted_at",
                      "idx_media_exif_coords"):
            with self.subTest(pieza=pieza):
                self.assertIn(pieza, migracion)
                self.assertIn(pieza, schema)


class RevisionIdLengthTests(unittest.TestCase):
    """`alembic_version.version_num` es VARCHAR(32) por defecto (lo crea el
    propio Alembic, no este proyecto). Una revision con un id mas largo pasa
    `alembic upgrade head` hasta el final y falla justo al final, en el
    UPDATE de `alembic_version` -- Postgres deshace toda la migracion por la
    transaccion, pero es un susto evitable con un check barato. Paso de
    verdad con "0004_collaborator_permission_levels" (35 caracteres)."""

    def test_ninguna_revision_supera_los_32_caracteres_de_alembic_version(self):
        for archivo in sorted((ROOT / "migrations/versions").glob("[0-9]*.py")):
            texto = archivo.read_text(encoding="utf-8")
            match = re.search(r'^revision = "([^"]+)"', texto, re.MULTILINE)
            with self.subTest(archivo=archivo.name):
                self.assertIsNotNone(match, "no se encontro 'revision = \"...\"'")
                self.assertLessEqual(len(match.group(1)), 32, f"'{match.group(1)}' tiene {len(match.group(1))} caracteres")


class RevisionMediaContextTests(unittest.TestCase):
    def test_la_migracion_0003_y_schema_sql_describen_las_mismas_tablas(self):
        migracion = (ROOT / "migrations/versions/0003_location_context.py").read_text(encoding="utf-8")
        schema = (ROOT / "schemas/schema.sql").read_text(encoding="utf-8")
        for pieza in (
            "media_context", "place_display_name", "locality", "region",
            "country_code", "country_name", "timezone", "location_provider",
            "location_enriched_at", "integration_usage", "period_key", "request_count",
        ):
            with self.subTest(pieza=pieza):
                self.assertIn(pieza, migracion)
                self.assertIn(pieza, schema)


class RevisionMediaChecksumTests(unittest.TestCase):
    def test_0020_adds_a_nullable_checked_non_unique_checksum_index(self):
        migration = (ROOT / "migrations/versions/0020_media_checksums.py").read_text(encoding="utf-8")
        schema = (ROOT / "schemas/schema.sql").read_text(encoding="utf-8")
        self.assertIn('revision = "0020_media_checksums"', migration)
        self.assertIn('down_revision = "0019_indexed_search"', migration)
        for text in (migration, schema):
            self.assertRegex(text, r"sha256\s+CHAR\(64\)\s+NULL")
            self.assertIn("chk_media_metadata_sha256", text)
            self.assertIn("idx_media_metadata_sha256", text)
            self.assertNotIn("UNIQUE INDEX idx_media_metadata_sha256", text.upper())

    def test_0020_downgrade_removes_every_checksum_object(self):
        migration = (ROOT / "migrations/versions/0020_media_checksums.py").read_text(encoding="utf-8")
        downgrade = migration.split("def downgrade():", 1)[1]
        self.assertIn("DROP INDEX IF EXISTS idx_media_metadata_sha256", downgrade)
        self.assertIn("DROP CONSTRAINT IF EXISTS chk_media_metadata_sha256", downgrade)
        self.assertIn("DROP COLUMN IF EXISTS sha256", downgrade)


class RevisionLibraryTimelineTests(unittest.TestCase):
    def test_0021_adds_the_same_partial_expression_index_to_migration_and_schema(self):
        migration_path = ROOT / "migrations/versions/0021_library_timeline.py"
        self.assertTrue(migration_path.exists())
        migration = migration_path.read_text(encoding="utf-8")
        schema = (ROOT / "schemas/schema.sql").read_text(encoding="utf-8")
        self.assertIn('revision = "0021_library_timeline"', migration)
        self.assertIn('down_revision = "0020_media_checksums"', migration)
        for text in (migration, schema):
            self.assertIn("idx_media_library_timeline", text)
            self.assertRegex(
                text,
                r"COALESCE\s*\(\s*taken_at\s*,\s*created_at\s*\)\s*\)?\s+DESC",
            )
            self.assertRegex(text, r"id\s+DESC")
            self.assertRegex(text, r"WHERE\s+deleted_at\s+IS\s+NULL")

    def test_0021_downgrade_removes_the_timeline_index(self):
        migration = (
            ROOT / "migrations/versions/0021_library_timeline.py"
        ).read_text(encoding="utf-8")
        downgrade = migration.split("def downgrade():", 1)[1]
        self.assertIn("DROP INDEX IF EXISTS idx_media_library_timeline", downgrade)


class RevisionOnThisDayHomeTests(unittest.TestCase):
    def test_0022_adds_the_same_partial_calendar_index_to_migration_and_schema(self):
        migration_path = ROOT / "migrations/versions/0022_on_this_day_home.py"
        self.assertTrue(migration_path.exists())
        migration = migration_path.read_text(encoding="utf-8")
        schema = (ROOT / "schemas/schema.sql").read_text(encoding="utf-8")
        self.assertIn('revision = "0022_on_this_day_home"', migration)
        self.assertIn('down_revision = "0021_library_timeline"', migration)
        for text in (migration, schema):
            self.assertIn("idx_media_on_this_day", text)
            self.assertIn("EXTRACT(MONTH FROM taken_at)", text)
            self.assertIn("EXTRACT(DAY FROM taken_at)", text)
            self.assertIn("EXTRACT(YEAR FROM taken_at)", text)
            self.assertIn("taken_at DESC", text)
            self.assertIn("deleted_at IS NULL AND taken_at IS NOT NULL", text)

    def test_0022_downgrade_removes_the_calendar_index(self):
        migration = (
            ROOT / "migrations/versions/0022_on_this_day_home.py"
        ).read_text(encoding="utf-8")
        downgrade = migration.split("def downgrade():", 1)[1]
        self.assertIn("DROP INDEX IF EXISTS idx_media_on_this_day", downgrade)


class RevisionMediaArchiveTests(unittest.TestCase):
    PATH = ROOT / "migrations/versions/0023_media_archive.py"

    def test_0023_adds_archived_at_and_the_archive_index_to_migration_and_schema(self):
        self.assertTrue(self.PATH.exists())
        migration = self.PATH.read_text(encoding="utf-8")
        schema = (ROOT / "schemas/schema.sql").read_text(encoding="utf-8")
        self.assertIn('revision = "0023_media_archive"', migration)
        self.assertIn('down_revision = "0022_on_this_day_home"', migration)
        self.assertIn("ADD COLUMN archived_at TIMESTAMP NULL", migration)
        self.assertIn("archived_at TIMESTAMP NULL", schema)
        # Sin zona, como deleted_at: NaiveDatetimeJSONProvider serializa ingenuo.
        self.assertNotIn("TIMESTAMPTZ", migration)
        for text in (migration, schema):
            self.assertIn("idx_media_archive", text)
            self.assertIn("(COALESCE(taken_at, created_at)) DESC", text)
            self.assertIn("deleted_at IS NULL AND archived_at IS NOT NULL", text)

    def test_0023_downgrade_refuses_to_silently_drop_archive_state(self):
        downgrade = self.PATH.read_text(encoding="utf-8").split("def downgrade():", 1)[1]
        self.assertIn("archived_at IS NOT NULL", downgrade)
        self.assertIn("raise RuntimeError", downgrade)
        self.assertLess(downgrade.index("raise RuntimeError"), downgrade.index("DROP COLUMN"))
        self.assertIn("DROP INDEX IF EXISTS idx_media_archive", downgrade)


class RevisionSmartAlbumsTests(unittest.TestCase):
    """L11: la definición de un álbum inteligente vive en su propia tabla."""

    PATH = ROOT / "migrations/versions/0025_smart_albums.py"

    def test_0025_follows_0024(self):
        self.assertTrue(self.PATH.exists())
        migration = self.PATH.read_text(encoding="utf-8")
        self.assertIn('revision = "0025_smart_albums"', migration)
        self.assertIn('down_revision = "0024_asset_album_membership"', migration)
        # La cabeza única la fija la revisión más reciente (L12, 0026).

    def test_0025_and_schema_sql_describe_the_same_table(self):
        migration = self.PATH.read_text(encoding="utf-8")
        schema = (ROOT / "schemas/schema.sql").read_text(encoding="utf-8")
        for texto in (migration, schema):
            self.assertIn("CREATE TABLE smart_albums", texto)
            self.assertIn("user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE", texto)
            self.assertIn("titulo VARCHAR(100) NOT NULL", texto)
            self.assertIn("filters JSONB NOT NULL", texto)
            self.assertIn("CHECK (jsonb_typeof(filters) = 'object')", texto)
            self.assertIn("idx_smart_albums_user", texto)
        # Sin zona, como el resto del esquema.
        self.assertNotIn("TIMESTAMPTZ", migration)

    def test_0025_downgrade_refuses_to_drop_saved_definitions(self):
        downgrade = self.PATH.read_text(encoding="utf-8").split("def downgrade():", 1)[1]
        self.assertIn("SELECT COUNT(*) FROM smart_albums", downgrade)
        self.assertIn("raise RuntimeError", downgrade)
        self.assertLess(downgrade.index("raise RuntimeError"), downgrade.index("DROP TABLE"))


def _heads() -> set[str]:
    revisiones, anteriores = set(), set()
    for archivo in (ROOT / "migrations/versions").glob("*.py"):
        texto = archivo.read_text(encoding="utf-8")
        revisiones.add(re.search(r'^revision = "([^"]+)"', texto, re.M).group(1))
        previa = re.search(r'^down_revision = "([^"]+)"', texto, re.M)
        if previa:
            anteriores.add(previa.group(1))
    return revisiones - anteriores


class RevisionActivityNotificationsTests(unittest.TestCase):
    """L12: actividad del álbum + notificaciones in-app, sin push."""

    PATH = ROOT / "migrations/versions/0026_activity_notifications.py"

    def test_0026_follows_0025_and_the_chain_keeps_one_head(self):
        self.assertTrue(self.PATH.exists())
        migration = self.PATH.read_text(encoding="utf-8")
        self.assertIn('revision = "0026_activity_notifications"', migration)
        self.assertIn('down_revision = "0025_smart_albums"', migration)
        # La cabeza única la fija la revisión más reciente.

    def test_0026_and_schema_sql_describe_the_same_tables(self):
        migration = self.PATH.read_text(encoding="utf-8")
        schema = (ROOT / "schemas/schema.sql").read_text(encoding="utf-8")
        for texto in (migration, schema):
            for fragmento in (
                "CREATE TABLE album_activity",
                "album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE",
                "subject_asset_id INTEGER NULL REFERENCES assets(id) ON DELETE SET NULL",
                "metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb",
                "CHECK (jsonb_typeof(metadata_json) = 'object')",
                "idx_album_activity_album",
                "CREATE TABLE notifications",
                "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE",
                "album_id INTEGER NULL REFERENCES albums(id) ON DELETE SET NULL",
                "idx_notifications_user",
                "idx_notifications_unread",
            ):
                self.assertIn(fragmento, texto)
        self.assertNotIn("TIMESTAMPTZ", migration)
        # La cabeza ya no tiene la bandera de push; la 0010 histórica sí, y no se toca.
        prefs = schema.split("CREATE TABLE user_notification_preferences", 1)[1].split(");", 1)[0]
        self.assertNotIn("web_push_enabled", prefs)
        self.assertIn("web_push_enabled", (ROOT / "migrations/versions/0010_notification_preferences.py").read_text(encoding="utf-8"))

    def test_0026_downgrade_refuses_to_drop_history(self):
        downgrade = self.PATH.read_text(encoding="utf-8").split("def downgrade():", 1)[1]
        self.assertIn('for tabla in ("album_activity", "notifications")', downgrade)
        self.assertIn('SELECT COUNT(*) FROM {tabla}', downgrade)
        self.assertIn("raise RuntimeError", downgrade)
        self.assertLess(downgrade.index("raise RuntimeError"), downgrade.index("DROP TABLE"))
        self.assertIn("web_push_enabled BOOLEAN NOT NULL DEFAULT FALSE", downgrade)


class RevisionRecoveryCodesTests(unittest.TestCase):
    """L14: códigos de recuperación, solo su hash."""

    PATH = ROOT / "migrations/versions/0027_recovery_codes.py"

    def test_0027_follows_0026(self):
        self.assertTrue(self.PATH.exists())
        migration = self.PATH.read_text(encoding="utf-8")
        self.assertIn('revision = "0027_recovery_codes"', migration)
        self.assertIn('down_revision = "0026_activity_notifications"', migration)

    def test_0027_and_schema_sql_describe_the_same_table(self):
        migration = self.PATH.read_text(encoding="utf-8")
        schema = (ROOT / "schemas/schema.sql").read_text(encoding="utf-8")
        for texto in (migration, schema):
            for fragmento in (
                "CREATE TABLE recovery_codes",
                "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE",
                "code_hash CHAR(64) NOT NULL UNIQUE",
                "CHECK (code_hash ~ '^[0-9a-f]{64}$')",
                "idx_recovery_codes_user",
            ):
                self.assertIn(fragmento, texto)

    def test_0027_downgrade_refuses_to_drop_codes(self):
        downgrade = self.PATH.read_text(encoding="utf-8").split("def downgrade():", 1)[1]
        self.assertIn("SELECT COUNT(*) FROM recovery_codes", downgrade)
        self.assertLess(downgrade.index("raise RuntimeError"), downgrade.index("DROP TABLE"))


class RevisionPasskeysTests(unittest.TestCase):
    """L15: passkeys. Solo datos públicos de la credencial; challenges hasheados."""

    PATH = ROOT / "migrations/versions/0028_webauthn_passkeys.py"

    def test_0028_follows_0027(self):
        migration = self.PATH.read_text(encoding="utf-8")
        self.assertIn('revision = "0028_webauthn_passkeys"', migration)
        self.assertIn('down_revision = "0027_recovery_codes"', migration)

    def test_0028_and_schema_sql_describe_the_same_tables(self):
        migration = self.PATH.read_text(encoding="utf-8")
        schema = (ROOT / "schemas/schema.sql").read_text(encoding="utf-8")
        for texto in (migration, schema):
            for fragmento in (
                "CREATE TABLE webauthn_credentials",
                "credential_id BYTEA NOT NULL UNIQUE",
                "CREATE TABLE webauthn_challenges",
                "challenge_hash CHAR(64) NOT NULL UNIQUE",
                "session_id BIGINT NULL REFERENCES user_sessions(id) ON DELETE CASCADE",
                "chk_webauthn_challenge_binding",
            ):
                self.assertIn(fragmento, texto)
        self.assertNotIn("private_key", migration)

    def test_0028_downgrade_refuses_to_drop_passkeys(self):
        downgrade = self.PATH.read_text(encoding="utf-8").split("def downgrade():", 1)[1]
        self.assertIn("SELECT COUNT(*) FROM webauthn_credentials", downgrade)
        self.assertLess(downgrade.index("raise RuntimeError"), downgrade.index("DROP TABLE"))


class SingleHeadTests(unittest.TestCase):
    def test_the_chain_has_exactly_one_head(self):
        """Dos cabezas harían ambiguo `alembic upgrade head`. Sea cual sea la
        última revisión, tiene que haber una sola."""
        self.assertEqual(1, len(_heads()), f"cabezas: {sorted(_heads())}")


class V1ReleaseChainTests(unittest.TestCase):
    """F09: el camino que F10 aplicará en producción, fijado de punta a punta.

    Producción está en 0023_media_archive. La v1 llega a 0029 subiendo UNA
    cadena lineal, y cada paso sabe negarse a bajar si bajar perdería datos.
    Si una revisión post-v1 aparece, esta prueba se actualiza a propósito:
    es el contrato del despliegue, no un detalle.
    """

    PRODUCTION = "0023_media_archive"
    V1_CHAIN = [
        "0024_asset_album_membership",
        "0025_smart_albums",
        "0026_activity_notifications",
        "0027_recovery_codes",
        "0028_webauthn_passkeys",
        "0029_asset_comments",
    ]

    def _revisions(self):
        out = {}
        for archivo in (ROOT / "migrations/versions").glob("*.py"):
            texto = archivo.read_text(encoding="utf-8")
            revision = re.search(r'^revision = "([^"]+)"', texto, re.M).group(1)
            previa = re.search(r'^down_revision = "([^"]+)"', texto, re.M)
            out[revision] = (previa.group(1) if previa else None, texto)
        return out

    def test_production_reaches_the_v1_head_through_one_linear_chain(self):
        revisions = self._revisions()
        self.assertEqual({self.V1_CHAIN[-1]}, _heads())
        camino, actual = [], self.V1_CHAIN[-1]
        while actual != self.PRODUCTION:
            camino.append(actual)
            actual = revisions[actual][0]
        self.assertEqual(self.V1_CHAIN, camino[::-1])

    def test_every_v1_step_refuses_a_lossy_downgrade(self):
        revisions = self._revisions()
        for revision in self.V1_CHAIN:
            downgrade = revisions[revision][1].split("def downgrade():", 1)[1]
            self.assertIn("raise RuntimeError", downgrade, revision)
