import unittest
from pathlib import Path

from werkzeug.datastructures import MultiDict

from app.media.assets import ACTIVE_SCOPE_SQL

from app.search import (
    build_media_search,
    normalize_search_text,
    parse_search_filters,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/versions/0019_indexed_search.py"
SCHEMA = ROOT / "schemas/schema.sql"


class SearchNormalizationTests(unittest.TestCase):
    def test_case_accents_and_unicode_spacing_are_normalized(self):
        self.assertEqual(
            "viaje nino sao tome",
            normalize_search_text("  VIAJE\u00a0Niño — São Tomé  "),
        )

    def test_characters_without_a_portable_database_fold_stay_stable(self):
        self.assertEqual("straße", normalize_search_text("Straße"))

    def test_python_contract_preserves_ligatures_and_non_latin_letters(self):
        self.assertEqual("æsir", normalize_search_text("Æsir"))
        self.assertEqual("йога", normalize_search_text("Йога"))

    def test_case_folding_is_explicit_for_known_cross_runtime_differences(self):
        self.assertEqual("istanbul", normalize_search_text("İstanbul"))
        self.assertEqual("kelvin", normalize_search_text("Kelvin"))
        self.assertEqual("ǆungla", normalize_search_text("ǅungla"))

    def test_explicit_combining_marks_match_precomposed_accents(self):
        self.assertEqual("ecole", normalize_search_text("e\u0301cole"))
        self.assertEqual("ecole", normalize_search_text("école"))

    def test_query_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "200"):
            parse_search_filters(MultiDict({"q": "x" * 201}))


class SearchFilterValidationTests(unittest.TestCase):
    def test_every_supported_filter_is_typed(self):
        filters = parse_search_filters(MultiDict({
            "q": "Samborondón",
            "media_type": "image",
            "date_from": "2024-01-02",
            "date_to": "2025-03-04",
            "year": "2024",
            "album_id": "12",
            "favorite": "true",
            "tag_id": "7",
            "place": "Guayas",
            "sort": "taken_at",
            "direction": "asc",
        }))

        self.assertEqual("samborondon", filters.query)
        self.assertEqual("image", filters.media_type)
        self.assertEqual(2024, filters.year)
        self.assertEqual(12, filters.album_id)
        self.assertIs(filters.favorite, True)
        self.assertEqual(7, filters.tag_id)
        self.assertEqual("guayas", filters.place)
        self.assertEqual("taken_at", filters.sort)
        self.assertEqual("asc", filters.direction)

    def test_invalid_allowlisted_values_are_rejected(self):
        invalid = (
            ("media_type", "audio"),
            ("favorite", "yes"),
            ("sort", "m.created_at; DROP TABLE media"),
            ("direction", "sideways"),
            ("album_id", "-1"),
            ("tag_id", "0"),
            ("year", "20x4"),
            ("date_from", "2024-99-99"),
        )
        for key, value in invalid:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                parse_search_filters(MultiDict({"q": "foto", key: value}))

    def test_date_range_must_be_ordered(self):
        with self.assertRaisesRegex(ValueError, "desde"):
            parse_search_filters(MultiDict({
                "q": "foto", "date_from": "2025-01-02", "date_to": "2025-01-01",
            }))

    def test_date_and_year_upper_boundary_cannot_overflow(self):
        for key, value in (("date_to", "9999-12-31"), ("year", "9999")):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "9998"):
                parse_search_filters(MultiDict({"q": "foto", key: value}))

    def test_a_query_or_filter_is_required(self):
        with self.assertRaisesRegex(ValueError, "criterio"):
            parse_search_filters(MultiDict())


class SearchSqlTests(unittest.TestCase):
    def test_owner_scope_and_active_state_are_always_first_class_conditions(self):
        built = build_media_search(parse_search_filters(MultiDict({"q": "playa"})), owner_id=9)
        self.assertIn("m.user_id = :user_id", built.where_sql)
        # L10A: el estado activo sale de las pertenencias del asset (suelto o en
        # algun album activo), no de un JOIN externo a albums.
        self.assertIn(ACTIVE_SCOPE_SQL, built.where_sql)
        self.assertIn("sa1.active = TRUE", built.where_sql)
        self.assertIn("m.deleted_at IS NULL", built.where_sql)
        self.assertNotRegex(built.where_sql, r"(?<![\w.])a\.")
        self.assertEqual(9, built.params["user_id"])

    def test_text_search_uses_the_indexed_vector_and_weighted_ranking(self):
        built = build_media_search(parse_search_filters(MultiDict({"q": "Niño playa"})), owner_id=4)
        self.assertIn("m.search_vector @@ to_tsquery('simple', :tsquery)", built.where_sql)
        self.assertIn("m.search_title = :query", built.order_sql)
        self.assertIn("ts_rank_cd(m.search_vector", built.order_sql)
        self.assertEqual("nino:* & playa:*", built.params["tsquery"])

    def test_all_user_values_remain_bound_parameters(self):
        attack = "x') OR TRUE; DROP TABLE media; --"
        built = build_media_search(parse_search_filters(MultiDict({
            "q": attack,
            "place": attack,
            "album_id": "3",
            "tag_id": "5",
        })), owner_id=8)
        self.assertNotIn(attack, built.where_sql)
        self.assertNotIn(attack, built.order_sql)
        self.assertIn("album_id", built.params)
        self.assertIn("tag_id", built.params)
        self.assertIn("place_tsquery", built.params)

    def test_structured_filters_are_applied_without_provider_calls(self):
        built = build_media_search(parse_search_filters(MultiDict({
            "media_type": "video", "year": "2020", "favorite": "false",
            "album_id": "2", "tag_id": "6", "place": "Quito",
        })), owner_id=1)
        for fragment in (
            "m.file_type = :media_type",
            "m.taken_at >= :year_start",
            "m.taken_at < :year_end",
            "m.is_favorite = :favorite",
            "ia.id = :album_id",
            "mt.tag_id = :tag_id",
            "m.search_place_vector @@ to_tsquery('simple', :place_tsquery)",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, built.where_sql)


class IndexedSearchMigrationTests(unittest.TestCase):
    def test_migration_is_portable_and_schema_snapshot_matches(self):
        migration = MIGRATION.read_text(encoding="utf-8")
        schema = SCHEMA.read_text(encoding="utf-8")
        for source in (migration, schema):
            with self.subTest(source="migration" if source is migration else "schema"):
                self.assertNotIn("CREATE EXTENSION", source.upper())
                self.assertNotIn("PG_TRGM", source.upper())
                self.assertNotIn("lower(COALESCE(value", source)
                self.assertIn("search_vector TSVECTOR", source)
                self.assertIn("USING GIN (search_vector)", source)
                self.assertIn("albumfp_refresh_media_search", source)
                self.assertIn("idx_media_search_owner_taken", source)

    def test_every_search_source_has_a_refresh_trigger(self):
        migration = MIGRATION.read_text(encoding="utf-8")
        schema = SCHEMA.read_text(encoding="utf-8")
        for table in ("media", "media_metadata", "media_exif", "media_context", "media_ocr", "media_tags", "tags"):
            with self.subTest(table=table):
                # La migración genera cinco triggers equivalentes desde una
                # allowlist; el snapshot deja visible el SQL ya expandido.
                self.assertIn(table, migration)
                self.assertIn(f" ON {table}", schema)

    def test_upgrade_contains_an_existing_data_backfill(self):
        migration = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("SELECT albumfp_refresh_media_search(id) FROM media", migration)

    def test_moved_child_rows_refresh_old_and_new_media(self):
        migration = MIGRATION.read_text(encoding="utf-8")
        schema = SCHEMA.read_text(encoding="utf-8")
        for source in (migration, schema):
            self.assertIn("OLD.media_id IS DISTINCT FROM NEW.media_id", source)
            self.assertIn("albumfp_refresh_media_search(OLD.media_id)", source)


class SearchEndpointSourceTests(unittest.TestCase):
    def test_global_search_uses_the_central_builder(self):
        source = (ROOT / "app/api/media.py").read_text(encoding="utf-8")
        start = source.index("def search_media")
        end = source.index("\n\n@media_bp", start)
        function = source[start:end]
        self.assertIn("parse_search_filters", function)
        # L11: la consulta vive en `run_media_search`, compartida con los
        # álbumes inteligentes; su forma es la misma de siempre.
        self.assertIn("run_media_search", function)
        search = (ROOT / "app/search.py").read_text(encoding="utf-8")
        executor = search[search.index("def run_media_search"):]
        self.assertIn("build_media_search", executor)
        self.assertNotIn("LIKE :q", executor)
        self.assertIn("WITH candidates AS MATERIALIZED", executor)
        self.assertIn("ORDER BY {built.order_sql}", executor)

    def test_album_search_uses_its_gin_vector(self):
        source = (ROOT / "app/api/albums.py").read_text(encoding="utf-8")
        start = source.index("def list_albums")
        end = source.index("\n\n@albums_bp", start)
        function = source[start:end]
        self.assertIn("a.search_vector @@ to_tsquery('simple', :search_tsquery)", function)
        self.assertNotIn("LOWER(COALESCE(a.titulo", function)
        self.assertNotIn('f"%{search}%"', function)

    def test_album_detail_search_uses_the_same_indexed_vectors(self):
        source = (ROOT / "app/api/media.py").read_text(encoding="utf-8")
        start = source.index("def list_album_media")
        end = source.index("\n\n@media_bp", start)
        function = source[start:end]
        self.assertIn("normalize_search_text", function)
        self.assertIn("m.search_vector @@ to_tsquery('simple', :search_tsquery)", function)
        self.assertIn("m.search_place_vector @@ to_tsquery('simple', :place_tsquery)", function)
        self.assertNotIn("LIKE :q", function)


if __name__ == "__main__":
    unittest.main()
