"""Un fallo del origin es 503, no 500 (spec: degradacion elegante).

La arquitectura entera existe para que la caida del local workstation sea un servicio
degradado, no una app rota. Si `StorageUnavailable` sale como 500 generico,
el navegador no puede distinguir "vuelve en un minuto" de "esto es un bug", y
el operador no tiene senal en el log de que el problema es el enlace privado.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask

from app.errors import register_error_handlers
from app.storage.contracts import (
    InvalidStorageKey,
    ObjectNotFound,
    StorageAuthenticationError,
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
    def test_una_caida_del_origin_es_503_y_no_500(self):
        for exc in (
            StorageUnavailable("origin caido"),
            StorageTimeout("tarde demasiado"),
            StorageAuthenticationError("token rechazado"),
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
