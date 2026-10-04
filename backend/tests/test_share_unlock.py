"""S08 -- nombre de la cookie de desbloqueo de un enlace publico con
contrasena. `create_unlock`/`is_unlocked`/`purge_expired_unlocks` son
envoltorios finos sobre una tabla (mismo criterio que `create_session` en
sessions.py, que tampoco tiene test puro): se verifican en la QA en vivo
contra una base real, no aqui.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.security.share_unlock import unlock_cookie_name
from app.security.sessions import hash_token


class UnlockCookieNameTests(unittest.TestCase):
    def test_se_deriva_del_token_no_es_un_nombre_fijo(self):
        nombre_a = unlock_cookie_name("token-a")
        nombre_b = unlock_cookie_name("token-b")
        self.assertNotEqual(nombre_a, nombre_b)

    def test_es_deterministico_para_el_mismo_token(self):
        self.assertEqual(unlock_cookie_name("mismo-token"), unlock_cookie_name("mismo-token"))

    def test_no_expone_el_token_ni_su_hash_completo(self):
        token = "un-token-cualquiera"
        nombre = unlock_cookie_name(token)
        self.assertNotIn(token, nombre)
        self.assertNotIn(hash_token(token), nombre)  # el hash completo no, solo un prefijo corto

    def test_sin_https_no_lleva_el_prefijo_host(self):
        with patch.dict("os.environ", {"APP_ENV": "development"}, clear=False):
            self.assertFalse(unlock_cookie_name("token").startswith("__Host-"))

    def test_con_https_en_produccion_lleva_el_prefijo_host(self):
        with patch.dict("os.environ", {"APP_ENV": "production", "HTTPS_ENABLED": "true"}, clear=False):
            self.assertTrue(unlock_cookie_name("token").startswith("__Host-"))


if __name__ == "__main__":
    unittest.main()
