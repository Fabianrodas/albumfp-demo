"""L11: el contrato de filtros guardados de un álbum inteligente.

Un álbum inteligente guarda un JSON de filtros, nunca SQL. Estas pruebas son
puras (sin base): fijan la lista exacta de claves, la normalización canónica y
el rechazo de todo lo que no sea un criterio de búsqueda válido. La ejecución
reutiliza `parse_search_filters`/`build_media_search`, así que aquí también se
comprueba que un filtro guardado produce la misma búsqueda que la global.
"""
import json
import unittest

from app.search import (
    SAVED_FILTER_NAMES,
    build_media_search,
    normalize_saved_filters,
    parse_search_filters,
    saved_filters_to_search,
)


class SavedFilterContractTests(unittest.TestCase):
    def test_the_allowlist_is_exactly_the_ten_current_search_criteria(self):
        self.assertEqual(
            {"q", "media_type", "date_from", "date_to", "year", "album_id",
             "favorite", "tag_id", "place", "archived"},
            set(SAVED_FILTER_NAMES),
        )

    def test_normalizes_to_a_canonical_typed_definition(self):
        self.assertEqual(
            {"q": "playa", "media_type": "image", "year": 2026, "favorite": True,
             "tag_id": 4, "archived": "exclude"},
            normalize_saved_filters({"q": "  playa ", "media_type": "image", "year": 2026,
                                     "favorite": True, "tag_id": 4, "archived": "exclude"}),
        )

    def test_empty_values_are_dropped_instead_of_stored_as_placeholders(self):
        self.assertEqual({"year": 2026},
                         normalize_saved_filters({"q": "  ", "place": "", "tag_id": None, "year": 2026}))

    def test_equivalent_input_has_one_stable_representation(self):
        a = normalize_saved_filters({"year": 2026, "q": "rio", "date_from": "2026-01-05"})
        b = normalize_saved_filters({"date_from": "2026-01-05", "q": " rio", "year": 2026})
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual(list(a), sorted(a), "las claves salen ordenadas")

    def test_dates_are_stored_as_iso_strings(self):
        self.assertEqual({"date_from": "2026-01-05", "date_to": "2026-02-01"},
                         normalize_saved_filters({"date_from": "2026-01-05", "date_to": "2026-02-01"}))

    def test_favorite_false_is_a_real_criterion(self):
        self.assertEqual({"favorite": False}, normalize_saved_filters({"favorite": False}))

    def test_the_legacy_example_is_rejected_but_its_current_equivalent_works(self):
        with self.assertRaises(ValueError):
            normalize_saved_filters({"country_code": "EC", "year": 2026, "tags": [3], "favorite": True})
        self.assertEqual({"favorite": True, "tag_id": 3, "year": 2026},
                         normalize_saved_filters({"year": 2026, "tag_id": 3, "favorite": True}))


class HostileSavedFilterTests(unittest.TestCase):
    CASES = {
        "not an object (list)": ["year", 2026],
        "not an object (string)": "year=2026",
        "not an object (null)": None,
        "no criterion at all": {},
        "only empty values": {"q": "", "place": "  "},
        "unknown key": {"year": 2026, "country_code": "EC"},
        "legacy tags array": {"tags": [3]},
        "legacy tag_ids": {"tag_ids": [3]},
        "sql-looking key": {"1=1; DROP TABLE assets": 1},
        "operator-looking key": {"year__gte": 2020},
        "mongo-style operator": {"$gt": 1},
        "sort persisted": {"year": 2026, "sort": "title"},
        "direction persisted": {"year": 2026, "direction": "asc"},
        "page persisted": {"year": 2026, "page": 2},
        "per_page persisted": {"year": 2026, "per_page": 100},
        "nested object value": {"year": {"gte": 2020}},
        "array for a scalar": {"media_type": ["image"]},
        "negative album id": {"album_id": -3},
        "zero tag id": {"tag_id": 0},
        "boolean masquerading as id": {"album_id": True},
        "string id": {"tag_id": "4"},
        "float id": {"album_id": 3.5},
        "bad date": {"date_from": "2026-13-01"},
        "date as number": {"date_from": 20260101},
        "date range inverted": {"date_from": "2026-02-01", "date_to": "2026-01-01"},
        "date_to at the sentinel": {"date_to": "9999-12-31"},
        "year below range": {"year": 0},
        "year above range": {"year": 9999},
        "year as string": {"year": "2026"},
        "year as boolean": {"year": True},
        "oversized q": {"q": "x" * 201},
        "oversized place": {"place": "x" * 121},
        "q not a string": {"q": 5},
        "invalid media_type": {"media_type": "audio"},
        "favorite as string": {"favorite": "true"},
        "favorite as number": {"favorite": 1},
        "invalid archived": {"archived": "all"},
        "q that normalizes to nothing": {"q": "¡¡!!"},
    }

    def test_every_hostile_definition_is_rejected(self):
        for label, raw in self.CASES.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    normalize_saved_filters(raw)


class SavedFilterExecutionTests(unittest.TestCase):
    def test_a_saved_definition_builds_the_same_search_as_the_global_endpoint(self):
        saved = normalize_saved_filters({"q": "playa", "year": 2026, "favorite": True, "tag_id": 7})
        global_search = parse_search_filters({"q": "playa", "year": "2026", "favorite": "true", "tag_id": "7"})
        self.assertEqual(global_search, saved_filters_to_search(saved))

    def test_ordering_uses_the_normal_search_defaults(self):
        self.assertEqual("relevance", saved_filters_to_search({"q": "playa"}).sort)
        without_text = saved_filters_to_search({"year": 2026})
        self.assertEqual(("created_at", "desc"), (without_text.sort, without_text.direction))

    def test_archived_semantics_follow_global_search(self):
        for mode, clause in (("only", "m.archived_at IS NOT NULL"), ("exclude", "m.archived_at IS NULL")):
            with self.subTest(mode):
                built = build_media_search(saved_filters_to_search({"year": 2026, "archived": mode}), owner_id=1)
                self.assertIn(clause, built.where_sql)
        default = build_media_search(saved_filters_to_search({"year": 2026}), owner_id=1)
        self.assertNotIn("archived_at", default.where_sql, "sin 'archived' entran archivados y no archivados")
        self.assertIn("m.deleted_at IS NULL", default.where_sql, "la papelera nunca entra")

    def test_sql_punctuation_in_a_value_stays_a_bound_parameter(self):
        texto = "'; DROP TABLE assets; -- x"
        saved = normalize_saved_filters({"q": texto, "place": texto})
        self.assertEqual(texto.strip(), saved["q"], "se guarda el texto tal cual, recortado")
        built = build_media_search(saved_filters_to_search(saved), owner_id=1)
        for fragmento in ("DROP", "assets;", "--"):
            self.assertNotIn(fragmento, built.where_sql)
        self.assertIn("drop:*", built.params["tsquery"])
        self.assertIn("drop:*", built.params["place_tsquery"])

    def test_a_stored_definition_that_went_bad_is_rejected_when_run(self):
        with self.assertRaises(ValueError):
            saved_filters_to_search({"year": 2026, "sort": "title"})


if __name__ == "__main__":
    unittest.main()
