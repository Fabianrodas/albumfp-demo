import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask

from app.utils.json_provider import NaiveDatetimeJSONProvider


def app_con_proveedor():
    app = Flask(__name__)
    app.json = NaiveDatetimeJSONProvider(app)
    return app


class SerializacionDeFechasTests(unittest.TestCase):
    """Todo TIMESTAMP de este esquema es ingenuo (sin zona) y representa hora
    local de Postgres o del EXIF/formulario. Flask por defecto le pone "GMT" a
    cualquier datetime, lo que hace que el navegador lo reinterprete como UTC
    real y lo desplace otra vez al formatear en su propia zona. Esa es la causa
    de que la fecha de captura EXIF apareciera 5 horas antes de la real.
    """

    def test_un_datetime_ingenuo_se_serializa_sin_marca_de_zona(self):
        app = app_con_proveedor()
        with app.app_context():
            texto = app.json.dumps({"cuando": datetime(2026, 8, 20, 15, 35, 3)})
        self.assertIn('"2026-08-20T15:35:03"', texto)
        # Justo lo que causaba el desfase: nunca debe volver a aparecer.
        self.assertNotIn("GMT", texto)

    def test_un_datetime_con_zona_sigue_el_camino_normal_de_flask(self):
        # Ninguno existe hoy en el backend, pero si alguna vez aparece uno no
        # hay que tocar su comportamiento: ya trae su propio offset y
        # `http_date()` lo convierte a GMT correctamente, sin mentir.
        app = app_con_proveedor()
        consciente = datetime(2026, 8, 20, 15, 35, 3, tzinfo=timezone.utc)
        with app.app_context():
            texto = app.json.dumps({"cuando": consciente})
        self.assertIn("Thu, 20 Aug 2026 15:35:03 GMT", texto)

    def test_decimal_y_uuid_siguen_funcionando_como_antes(self):
        import decimal
        import uuid
        app = app_con_proveedor()
        with app.app_context():
            texto = app.json.dumps({
                "precio": decimal.Decimal("12.34"),
                "id": uuid.UUID("12345678-1234-5678-1234-567812345678"),
            })
        self.assertIn('"12.34"', texto)
        self.assertIn('"12345678-1234-5678-1234-567812345678"', texto)

    def test_algo_realmente_no_serializable_sigue_fallando(self):
        app = app_con_proveedor()
        with app.app_context():
            with self.assertRaises(TypeError):
                app.json.dumps({"raro": object()})


class RedondeoDeFechaCapturaTests(unittest.TestCase):
    """Reproduce exactamente el desfase que reporto el usuario: 15:35 local
    convertido a "...GMT" y despues formateado en un navegador de la misma
    zona (America/Bogota, UTC-5) mostraba 10:35. Con la serializacion nueva,
    el navegador ya no tiene nada que convertir."""

    def test_ida_y_vuelta_sin_perder_la_hora(self):
        import json
        app = app_con_proveedor()
        original = datetime(2026, 8, 20, 15, 35, 3)
        with app.app_context():
            texto = app.json.dumps({"taken_at": original})
        de_vuelta = datetime.fromisoformat(json.loads(texto)["taken_at"])
        self.assertEqual(original, de_vuelta)


if __name__ == "__main__":
    unittest.main()
