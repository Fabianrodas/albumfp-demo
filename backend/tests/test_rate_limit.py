"""S03 -- limitador de intentos por ventana fija y la IP del cliente.

Sin Postgres (el patron de este repo): `try_consume`/`purge_expired_counters`
se ejercitan con `execute_safe` sustituido, igual que `test_sessions.py`
prueba `create_session`. El comportamiento contra una base real se verifica
en QA en vivo.
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from app.security import rate_limit


class TryConsumeShapeTests(unittest.TestCase):
    """El SQL en si (no su resultado, que depende de la base) es lo que se
    puede fijar sin Postgres: que el UPDATE atomico compare contra el limite
    y que la clave incluya scope+identifier+window_key."""

    def _sql_de(self, funcion):
        import ast
        import inspect
        arbol = ast.parse(inspect.getsource(funcion))
        return "\n".join(
            n.value for n in ast.walk(arbol)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        )

    def test_the_update_is_atomic_and_scoped_by_the_full_key(self):
        fuente = self._sql_de(rate_limit.try_consume)
        self.assertIn("request_count < :limit", fuente)
        self.assertIn("scope = :scope AND identifier = :identifier AND window_key = :window_key", fuente)
        self.assertIn("RETURNING request_count", fuente)

    def test_a_limit_of_zero_or_less_always_denies_without_touching_the_database(self):
        with patch.object(rate_limit, "execute_safe") as espia:
            permitido, retry_after = rate_limit.try_consume(object(), "login", "alguien", 0, 900)
        self.assertFalse(permitido)
        self.assertGreater(retry_after, 0)
        espia.assert_not_called()

    def test_the_window_key_is_stable_within_the_same_window(self):
        """Dos llamadas dentro de la MISMA ventana de 900s deben producir la
        misma window_key -- si no, cada llamada crearia su propia fila y el
        limite nunca se alcanzaria de verdad."""
        capturados = []

        def espia(conn, sql, params=None):
            capturados.append(dict(params or {}))

            class Resultado:
                def first(self_inner):
                    return {"request_count": 1}

            return Resultado()

        base = datetime(2026, 8, 29, 12, 0, 5)
        con_5_segundos_de_diferencia = base + timedelta(seconds=5)
        with patch.object(rate_limit, "execute_safe", espia):
            rate_limit.try_consume(object(), "login", "x", 10, 900, now=base)
            rate_limit.try_consume(object(), "login", "x", 10, 900, now=con_5_segundos_de_diferencia)

        claves = {p["window_key"] for p in capturados if "window_key" in p}
        self.assertEqual(1, len(claves), f"se esperaba UNA sola window_key, hubo: {claves}")

    def test_retry_after_never_reaches_or_exceeds_the_window_length(self):
        with patch.object(rate_limit, "execute_safe") as espia:
            espia.return_value.first.return_value = {"request_count": 1}
            _, retry_after = rate_limit.try_consume(object(), "login", "x", 10, 900)
        self.assertGreaterEqual(retry_after, 1)
        self.assertLessEqual(retry_after, 900)


class PurgeExpiredCountersTests(unittest.TestCase):
    def test_it_deletes_by_window_ends_at_with_a_cutoff(self):
        import ast
        import inspect
        arbol = ast.parse(inspect.getsource(rate_limit.purge_expired_counters))
        fuente = "\n".join(
            n.value for n in ast.walk(arbol)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        )
        self.assertIn("window_ends_at < :corte", fuente)


class ClientIpTrustBoundaryTests(unittest.TestCase):
    """`client_ip()` no hace nada propio: confirma que hereda exactamente lo
    que ya decide `ProxyFix`, cableado en `app.py` detras de
    `TRUST_PROXY_HEADERS`. Sin confianza, un XFF falsificado no tiene efecto;
    con confianza y un salto configurado, solo se lee el salto correcto."""

    def _app_con_ruta(self, proxy_fix=None):
        app = Flask(__name__)
        if proxy_fix:
            app.wsgi_app = ProxyFix(app.wsgi_app, **proxy_fix)

        @app.get("/ip")
        def ver_ip():
            return {"ip": rate_limit.client_ip()}

        return app

    def test_a_spoofed_forwarded_for_is_ignored_when_trust_is_disabled(self):
        app = self._app_con_ruta(proxy_fix=None)
        cliente = app.test_client()
        respuesta = cliente.get("/ip", headers={"X-Forwarded-For": "192.0.2.5"})
        self.assertNotEqual("192.0.2.5", respuesta.get_json()["ip"])

    def test_with_trust_enabled_for_one_hop_the_declared_client_is_used(self):
        app = self._app_con_ruta(proxy_fix={"x_for": 1})
        cliente = app.test_client()
        respuesta = cliente.get("/ip", headers={"X-Forwarded-For": "192.0.2.6"})
        self.assertEqual("192.0.2.6", respuesta.get_json()["ip"])

    def test_with_trust_enabled_for_one_hop_a_second_spoofed_hop_is_not_trusted(self):
        """Confia solo en el numero de saltos configurado (1): un segundo
        valor en la cadena (que solo un proxy de verdad podria haber
        añadido) no se cuela como si fuera el salto de confianza."""
        app = self._app_con_ruta(proxy_fix={"x_for": 1})
        cliente = app.test_client()
        respuesta = cliente.get("/ip", headers={"X-Forwarded-For": "192.0.2.3, 192.0.2.6"})
        self.assertEqual("192.0.2.6", respuesta.get_json()["ip"])
        self.assertNotEqual("192.0.2.3", respuesta.get_json()["ip"])


if __name__ == "__main__":
    unittest.main()
