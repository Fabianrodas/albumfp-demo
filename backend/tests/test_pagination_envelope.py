"""El sobre de paginación que recibe el frontend.

`ok(..., pagination=...)` anida los extras en `meta`. Hasta P08 el frontend leía
`response.pagination` (primer nivel) y sus pruebas simulaban esa forma, así que
"Cargar más" nunca aparecía en la app real. Esta prueba fija el lado del
backend; `paged-list.spec.ts` fija el lado del cliente con la misma forma.
"""
import sys
import unittest
from pathlib import Path

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.utils.pagination import build_pagination_meta
from app.utils.responses import ok


class PaginationEnvelopeTests(unittest.TestCase):
    def test_paginated_ok_nests_pagination_under_meta(self):
        with Flask(__name__).app_context():
            response, status = ok(data=[1, 2], message="x", pagination=build_pagination_meta(1, 12, 13))
        payload = response.get_json()
        self.assertEqual(200, status)
        self.assertNotIn("pagination", payload)
        self.assertEqual(
            {"page": 1, "per_page": 12, "total": 13, "total_pages": 2},
            payload["meta"]["pagination"],
        )


if __name__ == "__main__":
    unittest.main()
