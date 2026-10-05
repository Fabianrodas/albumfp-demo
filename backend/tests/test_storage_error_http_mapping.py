"""Un fallo de almacenamiento local es 503, no 500."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask

from app.errors import register_error_handlers
from app.storage.contracts import (
    InvalidStorageKey,
    ObjectNotFound,
    StorageTimeout,
    StorageUnavailable,
)


def _app(exc: Exception) -> Flask:
    app = Flask(__name__)
    register_error_handlers(app)

    @app.get("/boom")
    def boom():
        raise exc

    return app


class StorageErrorMappingTests(unittest.TestCase):
    def test_un_fallo_de_almacenamiento_es_503_y_no_500(self):
        for exc in (
            StorageUnavailable("almacenamiento no disponible"),
            StorageTimeout("tarde demasiado"),
        ):
            with self.subTest(exc=type(exc).__name__):
                cliente = _app(exc).test_client()
                respuesta = cliente.get("/boom")
                self.assertEqual(503, respuesta.status_code)

    def test_un_objeto_ausente_es_404_no_503(self):
        respuesta = _app(ObjectNotFound("no esta")).test_client().get("/boom")
        self.assertEqual(404, respuesta.status_code)

    def test_una_key_invalida_es_400_no_503(self):
        respuesta = _app(InvalidStorageKey("key rara")).test_client().get("/boom")
        self.assertEqual(400, respuesta.status_code)

    def test_503_does_not_leak_internal_storage_details(self):
        exc = StorageUnavailable("synthetic internal storage detail")
        respuesta = _app(exc).test_client().get("/boom")
        cuerpo = respuesta.get_data(as_text=True)
        self.assertEqual(503, respuesta.status_code)
        for secret in ("synthetic internal storage detail",):
            self.assertNotIn(secret, cuerpo)
        self.assertNotIn("192.0.2.7", str(dict(respuesta.headers)))


if __name__ == "__main__":
    unittest.main()
