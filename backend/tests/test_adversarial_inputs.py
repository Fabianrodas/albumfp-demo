"""S16 — entradas hostiles contra las funciones que de verdad las reciben.

A diferencia de la matriz de autorización, esto SÍ ejecuta código real: los
validadores son puros, así que se les puede meter basura de verdad sin base de
datos ni red. Lo que se busca es el fallo que no se parece a un rechazo — una
excepción sin capturar (que sale como 500 en vez de 400), un `None` que pasa por
válido, un desbordamiento que se acepta en silencio.

No hay malware ni cargas destructivas: los ficheros hostiles son sintéticos y se
generan en memoria.
"""

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.rules import (
    normalize_capabilities,
    validate_capture_date,
    validate_coordinate,
    validate_share_password,
    validate_tag_ids,
)
from app.logging import redact_path
from app.security.password_policy import validate_password
from app.storage.object_keys import validate_storage_key
from app.utils.sql_security import safe_identifier, safe_text

# Basura que un cliente hostil puede mandar en cualquier campo de texto. Cada
# una ha roto algo en algún proyecto alguna vez.
HOSTILE_STRINGS = (
    "",
    " ",
    "\x00",                      # NUL: corta cadenas en C y en algunos drivers
    "a\x00b",                    # NUL en medio, no al final
    "\r\n",                      # inyección de cabecera
    "A" * 10_000,                # desbordar un campo
    "../../etc/passwd",          # travesía de rutas
    "..\\..\\windows\\system32", # travesía en la otra barra
    "'; DROP TABLE users; --",   # el clásico
    "<script>alert(1)</script>",
    "\u202e" + "gnp.txt",        # right-to-left override: disfraza extensiones
    "\U0001f4a9" * 100,          # fuera del BMP: rompe UCS-2
    "\ufeff",                    # BOM invisible
    "%2e%2e%2f",                 # travesía percent-encoded
    "${jndi:ldap://x/a}",
    "\N{COMBINING GRAVE ACCENT}" * 500,
)

# Números que rompen validaciones escritas a ojo.
HOSTILE_NUMBERS = (
    float("nan"), float("inf"), float("-inf"),
    10**20, -(10**20), 0, -0.0,
    "1e400", "0x10", "١٢٣",       # notación científica, hex, dígitos árabes
    None, True, [], {},
)


class NeverRaisesUnexpectedlyTests(unittest.TestCase):
    """El contrato de un validador es devolver un error, no reventar.

    Una excepción que no sea `ValueError` sale del endpoint como 500, y un 500
    le dice a un atacante que encontró algo que nadie previó.
    """

    def test_capture_date_rejects_garbage_without_crashing(self):
        for valor in HOSTILE_STRINGS + HOSTILE_NUMBERS:
            with self.subTest(valor=repr(valor)[:40]):
                try:
                    validate_capture_date(valor, "taken_at")
                except ValueError:
                    pass
                except Exception as exc:  # noqa: BLE001 - es justo lo que se busca
                    self.fail(f"{type(exc).__name__} en vez de ValueError: {exc}")

    def test_coordinate_rejects_garbage_without_crashing(self):
        for valor in HOSTILE_STRINGS + HOSTILE_NUMBERS:
            with self.subTest(valor=repr(valor)[:40]):
                try:
                    validate_coordinate(valor, -90, 90, "latitude")
                except ValueError:
                    pass
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"{type(exc).__name__} en vez de ValueError: {exc}")

    def test_tag_ids_reject_garbage_without_crashing(self):
        hostiles = ([], [None], ["1"], [1.5], [-1], [10**20], "no-es-lista",
                    [[1]], [{"id": 1}], list(range(10_000)), None, {"a": 1})
        for valor in hostiles:
            with self.subTest(valor=repr(valor)[:40]):
                try:
                    validate_tag_ids(valor)
                except ValueError:
                    pass
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"{type(exc).__name__} en vez de ValueError: {exc}")

    def test_capabilities_reject_garbage_without_crashing(self):
        hostiles = (None, "upload", ["upload", "upload"], ["UPLOAD"], [""],
                    ["admin"], [None], [1], list(range(1000)), {"a": 1}, [[]])
        for valor in hostiles:
            with self.subTest(valor=repr(valor)[:40]):
                try:
                    normalize_capabilities(valor)
                except ValueError:
                    pass
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"{type(exc).__name__} en vez de ValueError: {exc}")

    def test_share_password_rejects_garbage_without_crashing(self):
        for valor in HOSTILE_STRINGS + (None, 12345, [], {}):
            with self.subTest(valor=repr(valor)[:40]):
                try:
                    validate_share_password(valor)
                except ValueError:
                    pass
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"{type(exc).__name__} en vez de ValueError: {exc}")


class CoordinateBoundaryTests(unittest.TestCase):
    """Los límites, que es donde se cuelan los errores de un carácter."""

    def test_the_exact_boundaries_are_accepted(self):
        for valor in (-90, 90, -90.0, 90.0):
            with self.subTest(valor=valor):
                self.assertIsNotNone(validate_coordinate(valor, -90, 90, "latitude"))

    def test_just_outside_the_boundary_is_rejected(self):
        for valor in (90.0000001, -90.0000001, 91, -91, 180, -180):
            with self.subTest(valor=valor):
                with self.assertRaises(ValueError):
                    validate_coordinate(valor, -90, 90, "latitude")

    def test_nan_is_never_accepted_as_a_coordinate(self):
        """NaN es la trampa: TODA comparación con él es falsa, así que un
        `if not minimo <= x <= maximo` escrito al revés lo dejaría pasar y
        contaminaría la geocodificación."""
        with self.assertRaises(ValueError):
            validate_coordinate(float("nan"), -90, 90, "latitude")


class CaptureDateTests(unittest.TestCase):
    def test_a_partial_date_is_rejected_instead_of_being_filled_in(self):
        """Desde Python 3.11 `datetime.fromisoformat` acepta `2026` y `2026-08`
        y los RELLENA con enero y el día 1. Una fecha a medias se guardaría como
        si fuera exacta."""
        for parcial in ("2026", "2026-08", "2026-W32", "2026-08-", "--08-15"):
            with self.subTest(valor=parcial):
                with self.assertRaises(ValueError):
                    validate_capture_date(parcial, "taken_at")

    def test_a_full_date_survives(self):
        self.assertIsNotNone(validate_capture_date("2026-08-15", "taken_at"))
        self.assertIsNotNone(validate_capture_date("2026-08-15T14:30", "taken_at"))


class PasswordPolicyTests(unittest.TestCase):
    def test_the_local_rule_never_touches_the_network(self):
        """`validate_password` es la regla LOCAL y pura: el frontend la replica
        en `auth-validation.ts`. Si empezara a llamar a HIBP, esa copia dejaría
        de ser equivalente y el navegador aceptaría lo que el servidor rechaza."""
        import app.security.password_policy as modulo
        fuente = Path(modulo.__file__).read_text(encoding="utf-8")
        cuerpo = fuente[fuente.index("def validate_password"):]
        cuerpo = cuerpo[:cuerpo.index("\ndef ")]
        for prohibido in ("urlopen", "requests", "httpx", "pwned", "socket"):
            with self.subTest(prohibido=prohibido):
                self.assertNotIn(prohibido, cuerpo)

    def test_hostile_strings_are_rejected_or_accepted_but_never_crash(self):
        for valor in HOSTILE_STRINGS + (None, 1234, [], {}):
            with self.subTest(valor=repr(valor)[:40]):
                try:
                    validate_password(valor)
                except ValueError:
                    pass
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"{type(exc).__name__} en vez de ValueError: {exc}")

    def test_a_very_long_password_is_refused_rather_than_hashed(self):
        """Argon2 procesa la entrada entera: sin tope, una contraseña de
        megabytes es una forma barata de gastar CPU del servidor."""
        resultado = validate_password("a" * 100_000)
        self.assertTrue(resultado, "una contraseña enorme debe dar error, no pasar")


class StorageKeyTests(unittest.TestCase):
    """Una key de objeto viaja hasta una ruta de fichero en el origin. Una
    travesía aquí sale del árbol de media."""

    def test_traversal_and_absolute_paths_are_rejected(self):
        hostiles = ("../secret", "a/../../b", "/etc/passwd", "\\\\host\\share",
                    "C:\\Windows", "a//b", "./a", "a/./b", "", ".", "..",
                    "a\x00b", "a\nb", "\u202eb", "a/", "/a")
        for key in hostiles:
            with self.subTest(key=repr(key)):
                try:
                    validate_storage_key(key)
                except Exception:
                    continue
                self.fail(f"se acepto una key peligrosa: {key!r}")


class SqlHelperTests(unittest.TestCase):
    def test_an_identifier_never_accepts_a_dangerous_character(self):
        """Un nombre largo de caracteres validos SI se acepta, y es correcto:
        lo que de verdad acota es la allowlist, y los dos unicos llamadores
        (`sort_by` en dos listados) la pasan siempre. Lo que nunca debe pasar es
        un caracter con el que salirse del identificador."""
        # Fuera la cadena de 10.000 "A": es larga, pero sintacticamente un
        # identificador valido, y la allowlist es quien la rechaza.
        peligrosos = tuple(v for v in HOSTILE_STRINGS if v.strip("A"))
        for valor in peligrosos + ("a b", "a-b", "1abc", "a;b", "a'b", "a\"b", "a`b"):
            with self.subTest(valor=repr(valor)[:40]):
                with self.assertRaises(ValueError):
                    safe_identifier(valor)

    def test_stacked_statements_and_comments_are_refused(self):
        for consulta in ("SELECT 1; DELETE FROM users",
                         "SELECT 1 -- x",
                         "SELECT /* x */ 1",
                         "SELECT 1 */",
                         "",
                         "   "):
            with self.subTest(consulta=consulta):
                with self.assertRaises(ValueError):
                    safe_text(consulta)

    def test_an_allowlist_actually_constrains_the_identifier(self):
        self.assertEqual("created_at", safe_identifier("created_at", {"created_at", "taken_at"}))
        with self.assertRaises(ValueError):
            safe_identifier("password_hash", {"created_at", "taken_at"})


class LogRedactionTests(unittest.TestCase):
    """La redacción del token de enlace corre en CADA petición: si revienta con
    una ruta rara, tumba el logger de toda la app."""

    def test_redaction_survives_hostile_paths(self):
        for valor in HOSTILE_STRINGS:
            with self.subTest(valor=repr(valor)[:40]):
                self.assertIsInstance(redact_path(valor), str)

    def test_a_token_is_removed_however_the_path_continues(self):
        for ruta in ("/api/shared/SECRETO/media",
                     "/api/shared/SECRETO/media/7/preview",
                     "/shared/SECRETO",
                     "/api/shared/SECRETO"):
            with self.subTest(ruta=ruta):
                self.assertNotIn("SECRETO", redact_path(ruta))


class HostileUploadTests(unittest.TestCase):
    """Ficheros sintéticos, nunca malware. Lo que se comprueba es que la
    detección de formato no se deje engañar y que la decodificación real
    rechace lo que la firma sola no puede ver."""

    @staticmethod
    def _detect(data, filename):
        from app.storage.media_storage import detect_media_signature
        return detect_media_signature(io.BytesIO(data), filename)

    def test_a_fake_extension_never_decides_the_format(self):
        """Un ejecutable renombrado a .jpg: la extensión miente, los bytes no."""
        for datos, nombre in (
            (b"MZ\x90\x00" + b"\x00" * 64, "foto.jpg"),
            (b"\x7fELF" + b"\x00" * 64, "foto.png"),
            (b"#!/bin/sh\nrm -rf /\n", "video.mp4"),
            (b"<?php echo 1; ?>", "imagen.webp"),
            (b"", "vacio.jpg"),
            (b"\x00" * 32, "ceros.jpg"),
        ):
            with self.subTest(nombre=nombre):
                try:
                    resultado = self._detect(datos, nombre)
                except Exception:
                    continue  # rechazar lanzando tambien vale
                self.assertFalse(
                    resultado and resultado[0] in ("image", "video"),
                    f"{nombre}: se acepto por la extension pese a los bytes",
                )

    def test_a_valid_header_with_a_corrupt_body_is_rejected_by_the_decoder(self):
        """La firma de bytes NUNCA pretendió ser esta comprobación: una cabecera
        JPEG válida con el cuerpo roto solo la caza Pillow al decodificar."""
        from app.media.image_validation import validate_image
        import tempfile, os

        roto = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF" + b"\xff" * 200
        fd, ruta = tempfile.mkstemp(suffix=".jpg")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(roto)
            with self.assertRaises(Exception):
                validate_image(Path(ruta))
        finally:
            os.unlink(ruta)

    def test_a_real_image_still_passes(self):
        """El contrapeso: si la validación rechazara todo, estas pruebas
        pasarían sin significar nada."""
        from PIL import Image
        from app.media.image_validation import validate_image
        import tempfile, os

        fd, ruta = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        try:
            Image.new("RGB", (64, 48), (10, 120, 90)).save(ruta, "JPEG")
            validada = validate_image(Path(ruta))
            self.assertEqual((64, 48), (validada.width, validada.height))
            self.assertEqual("image/jpeg", validada.mime_type)
        finally:
            os.unlink(ruta)


if __name__ == "__main__":
    unittest.main()
