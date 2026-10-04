"""S04 -- lookup local de reputacion de IP.

Sin Postgres (el patron de este repo, ver test_rate_limit.py):
`execute_safe` se sustituye para fijar la FORMA del SQL y el contrato de
`reputation_score`/`is_high_risk`. El dato real contra AbuseIPDB lo cubre el
QA en vivo.
"""
import ast
import inspect
import unittest
from unittest.mock import patch

from app.security import ip_reputation


def _sql_de(funcion) -> str:
    arbol = ast.parse(inspect.getsource(funcion))
    return "\n".join(
        n.value for n in ast.walk(arbol)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    )


class ReputationScoreTests(unittest.TestCase):
    def test_una_ip_sin_fila_vale_cero_no_una_excepcion(self):
        with patch.object(ip_reputation, "execute_safe") as espia:
            espia.return_value.mappings.return_value.first.return_value = None
            self.assertEqual(0, ip_reputation.reputation_score(object(), "203.0.113.5"))

    def test_una_ip_con_fila_devuelve_su_confianza(self):
        with patch.object(ip_reputation, "execute_safe") as espia:
            espia.return_value.mappings.return_value.first.return_value = {"abuse_confidence": 100}
            self.assertEqual(100, ip_reputation.reputation_score(object(), "203.0.113.5"))

    def test_la_consulta_filtra_por_cache_vigente_y_la_ip_exacta(self):
        fuente = _sql_de(ip_reputation.reputation_score)
        self.assertIn("expires_at > NOW()", fuente)
        self.assertIn("network_or_ip = :ip", fuente)

    def test_la_ip_viaja_como_parametro_no_interpolada_en_el_sql(self):
        with patch.object(ip_reputation, "execute_safe") as espia:
            espia.return_value.mappings.return_value.first.return_value = None
            ip_reputation.reputation_score(object(), "203.0.113.5")
        self.assertEqual("203.0.113.5", espia.call_args.args[2]["ip"])

    def test_un_valor_que_no_es_una_ip_vale_cero_y_no_llega_a_la_base(self):
        """Bug real de S05: client_ip() puede devolver "desconocida" (su
        propio valor de respaldo). Esa cadena no es un INET valido, y sin
        esta guarda Postgres tira un error de sintaxis -- un 500 en pleno
        login -- en vez de tratarla como cualquier otra IP sin señal."""
        with patch.object(ip_reputation, "execute_safe") as espia:
            for valor in ("desconocida", "", "no-es-una-ip", "192.0.2.4.5"):
                with self.subTest(valor=valor):
                    self.assertEqual(0, ip_reputation.reputation_score(object(), valor))
        espia.assert_not_called()


class IsHighRiskTests(unittest.TestCase):
    def test_por_debajo_del_umbral_no_es_alto_riesgo(self):
        with patch.object(ip_reputation, "reputation_score", return_value=ip_reputation.HIGH_RISK_THRESHOLD - 1):
            self.assertFalse(ip_reputation.is_high_risk(object(), "203.0.113.5"))

    def test_en_el_umbral_ya_es_alto_riesgo(self):
        with patch.object(ip_reputation, "reputation_score", return_value=ip_reputation.HIGH_RISK_THRESHOLD):
            self.assertTrue(ip_reputation.is_high_risk(object(), "203.0.113.5"))

    def test_sin_señal_nunca_es_alto_riesgo(self):
        """Sin fila vigente (nunca reportada, o cache vencido porque el job
        no corrio) se trata como confianza 0 -- nunca como sospechoso."""
        with patch.object(ip_reputation, "reputation_score", return_value=0):
            self.assertFalse(ip_reputation.is_high_risk(object(), "203.0.113.5"))


if __name__ == "__main__":
    unittest.main()
