"""Cifrado reversible del token de enlace: ida y vuelta, y comportamiento
sin clave configurada (no debe reventar, solo dejar de estar disponible)."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.fernet import Fernet

from app.security import share_token_crypto as crypto


class ShareTokenCryptoTests(unittest.TestCase):
    def setUp(self):
        # El cliente Fernet se cachea en el modulo tras la primera llamada;
        # cada test necesita partir sin esa cache para que el patch de
        # SHARE_TOKEN_ENCRYPTION_KEY (o su ausencia) surta efecto de verdad.
        crypto._fernet = None
        crypto._fernet_cargado = False

    def test_ida_y_vuelta_con_clave_configurada(self):
        clave = Fernet.generate_key().decode()
        with patch.dict("os.environ", {"SHARE_TOKEN_ENCRYPTION_KEY": clave}):
            crypto._fernet_cargado = False
            cifrado = crypto.encrypt_share_token("token-de-prueba")
            self.assertIsNotNone(cifrado)
            self.assertNotIn("token-de-prueba", cifrado)
            self.assertEqual(crypto.decrypt_share_token(cifrado), "token-de-prueba")

    def test_sin_clave_configurada_no_revienta_solo_no_esta_disponible(self):
        with patch.dict("os.environ", {"SHARE_TOKEN_ENCRYPTION_KEY": ""}):
            crypto._fernet_cargado = False
            self.assertIsNone(crypto.encrypt_share_token("token"))
            self.assertIsNone(crypto.decrypt_share_token("cualquier-cosa"))

    def test_ciphertext_invalido_devuelve_none_no_lanza(self):
        clave = Fernet.generate_key().decode()
        with patch.dict("os.environ", {"SHARE_TOKEN_ENCRYPTION_KEY": clave}):
            crypto._fernet_cargado = False
            self.assertIsNone(crypto.decrypt_share_token("esto-no-es-un-fernet-valido"))

    def test_ninguna_entrada_vacia_intenta_descifrar(self):
        self.assertIsNone(crypto.decrypt_share_token(None))
        self.assertIsNone(crypto.decrypt_share_token(""))

    def test_dos_cifrados_del_mismo_token_no_son_iguales(self):
        """Fernet usa un nonce aleatorio por mensaje -- si esto fallara, dos
        enlaces con la misma sal de tiempo serian correlacionables."""
        clave = Fernet.generate_key().decode()
        with patch.dict("os.environ", {"SHARE_TOKEN_ENCRYPTION_KEY": clave}):
            crypto._fernet_cargado = False
            a = crypto.encrypt_share_token("mismo-token")
            crypto._fernet_cargado = False
            b = crypto.encrypt_share_token("mismo-token")
            self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
