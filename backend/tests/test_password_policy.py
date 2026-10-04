"""S02 -- politica de contraseñas alineada con NIST SP 800-63B: minimo mas
alto, sin reglas de composicion. Solo `validate_password`, la funcion local y
pura: no toca la red, asi que estas pruebas no mockean nada.
"""
import unittest

from app.security.password_policy import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, validate_password


class LengthPolicyTests(unittest.TestCase):
    def test_shorter_than_the_minimum_is_rejected(self):
        valido, error = validate_password("a" * (MIN_PASSWORD_LENGTH - 1))
        self.assertFalse(valido)
        self.assertIn(str(MIN_PASSWORD_LENGTH), error)

    def test_exactly_the_minimum_is_accepted(self):
        valido, error = validate_password("a" * MIN_PASSWORD_LENGTH)
        self.assertEqual((True, None), (valido, error))

    def test_exactly_the_configured_maximum_is_accepted(self):
        valido, error = validate_password("a" * MAX_PASSWORD_LENGTH)
        self.assertEqual((True, None), (valido, error))

    def test_longer_than_the_configured_maximum_is_rejected(self):
        valido, error = validate_password("a" * (MAX_PASSWORD_LENGTH + 1))
        self.assertFalse(valido)
        self.assertIn(str(MAX_PASSWORD_LENGTH), error)


class NoCompositionRulesTests(unittest.TestCase):
    """NIST SP 800-63B: nada de exigir mayuscula, simbolo o digito. Solo el
    minimo de longitud (y el guardia existente contra "solo numeros", que no
    es una regla de composicion -- no exige ninguna clase de caracter, solo
    descarta el caso degenerado de un PIN escrito como contraseña)."""

    def test_all_lowercase_letters_is_accepted(self):
        valido, _ = validate_password("a" * MIN_PASSWORD_LENGTH)
        self.assertTrue(valido)

    def test_no_uppercase_is_required(self):
        valido, _ = validate_password("contraseñaminuscula")
        self.assertTrue(valido)

    def test_no_symbol_is_required(self):
        valido, _ = validate_password("solo letras y numeros dos mil")
        self.assertTrue(valido)

    def test_spaces_are_allowed_nist_passphrase_style(self):
        # Construida por tamaño, no contada a ojo: alterna letra y espacio.
        frase = ("x " * ((MIN_PASSWORD_LENGTH // 2) + 1)).rstrip()
        self.assertGreaterEqual(len(frase), MIN_PASSWORD_LENGTH)
        valido, _ = validate_password(frase)
        self.assertTrue(valido)

    def test_unicode_characters_are_allowed(self):
        # Se ancla con relleno a la longitud minima, sin contar tildes a ojo.
        frase = ("áéíóúñ" + "x" * MIN_PASSWORD_LENGTH)[:max(MIN_PASSWORD_LENGTH, 6)]
        self.assertGreaterEqual(len(frase), MIN_PASSWORD_LENGTH)
        valido, _ = validate_password(frase)
        self.assertTrue(valido)

    def test_purely_numeric_is_still_rejected_regardless_of_length(self):
        valido, error = validate_password("1" * MIN_PASSWORD_LENGTH)
        self.assertFalse(valido)
        self.assertIn("números", error)


if __name__ == "__main__":
    unittest.main()
