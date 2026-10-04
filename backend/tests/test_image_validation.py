"""S06 -- validacion profunda de imagenes con Pillow.

Con archivos reales (mismo criterio que test_previews.py/test_external_copy.py):
lo que se comprueba -- que un archivo truncado, uno falso, o una decompression
bomb se detecten decodificando de verdad -- no se puede mockear sin dejar de
probarlo.
"""
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from pillow_heif import from_pillow

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.media.image_validation import ImageValidationError, validate_image

_TMP_DIR = Path(tempfile.mkdtemp(prefix="albumfp_test_image_validation_"))


def tearDownModule():
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


class ValidateImageTests(unittest.TestCase):
    def test_una_imagen_real_y_valida_pasa_con_sus_dimensiones_reales(self):
        archivo = _TMP_DIR / "valida.jpg"
        Image.new("RGB", (120, 80), "blue").save(archivo, format="JPEG")

        resultado = validate_image(archivo)

        self.assertEqual("JPEG", resultado.format)
        self.assertEqual("image/jpeg", resultado.mime_type)
        self.assertEqual(120, resultado.width)
        self.assertEqual(80, resultado.height)

    def test_formatos_distintos_dan_su_mime_canonico(self):
        casos = [("PNG", "image/png"), ("GIF", "image/gif"), ("WEBP", "image/webp")]
        for formato, mime_esperado in casos:
            with self.subTest(formato=formato):
                archivo = _TMP_DIR / f"valida.{formato.lower()}"
                Image.new("RGB", (10, 10), "green").save(archivo, format=formato)
                resultado = validate_image(archivo)
                self.assertEqual(mime_esperado, resultado.mime_type)

    def test_una_imagen_heic_real_pasa_con_mime_canonico(self):
        archivo = _TMP_DIR / "valida.heic"
        from_pillow(Image.new("RGB", (48, 32), "purple")).save(archivo, quality=90)

        resultado = validate_image(archivo)

        self.assertEqual("HEIF", resultado.format)
        self.assertEqual("image/heic", resultado.mime_type)
        self.assertEqual((48, 32), (resultado.width, resultado.height))

    def test_un_archivo_truncado_a_mitad_se_rechaza(self):
        original = _TMP_DIR / "origen.jpg"
        Image.new("RGB", (200, 200), "red").save(original, format="JPEG")
        completo = original.read_bytes()

        truncado = _TMP_DIR / "truncado.jpg"
        truncado.write_bytes(completo[: len(completo) // 2])

        with self.assertRaises(ImageValidationError):
            validate_image(truncado)

    def test_un_archivo_que_no_es_una_imagen_se_rechaza(self):
        fake = _TMP_DIR / "no_es_imagen.jpg"
        fake.write_bytes(b"esto es texto plano, no una imagen" * 20)

        with self.assertRaises(ImageValidationError):
            validate_image(fake)

    def test_un_archivo_vacio_se_rechaza(self):
        vacio = _TMP_DIR / "vacio.jpg"
        vacio.write_bytes(b"")

        with self.assertRaises(ImageValidationError):
            validate_image(vacio)

    def test_una_decompression_bomb_se_rechaza(self):
        """Dimensiones muy por encima de MAX_IMAGE_PIXELS -- modo '1' (1 bit
        por pixel) para que el archivo en si no sea gigante, solo lo que
        describe sean muchisimos pixeles."""
        bomba = _TMP_DIR / "bomba.png"
        Image.new("1", (20000, 20000)).save(bomba, format="PNG")

        with self.assertRaises(ImageValidationError):
            validate_image(bomba)

    def test_una_imagen_normal_no_se_confunde_con_una_bomba(self):
        """El limite es real, no un rechazo generico de todo: una foto de
        camara comun (por debajo de 64 megapixeles) debe seguir pasando."""
        normal = _TMP_DIR / "normal.jpg"
        Image.new("RGB", (4000, 3000), "yellow").save(normal, format="JPEG")  # 12 MP

        resultado = validate_image(normal)
        self.assertEqual(4000, resultado.width)

    def test_no_modifica_el_limite_global_de_pillow_incluso_si_rechaza(self):
        """El límite de AlbumFP se comprueba por imagen; no debe mutar la
        variable global de Pillow ni contaminar otros requests concurrentes."""
        original = Image.MAX_IMAGE_PIXELS
        fake = _TMP_DIR / "no_es_imagen_2.jpg"
        fake.write_bytes(b"no es una imagen")
        try:
            validate_image(fake)
        except ImageValidationError:
            pass
        self.assertEqual(original, Image.MAX_IMAGE_PIXELS)

    def test_validaciones_concurrentes_no_pueden_desactivar_el_limite_de_64_mp(self):
        """Dos requests no deben coordinarse a traves de MAX_IMAGE_PIXELS,
        que es estado global de Pillow y permite una carrera entre threads."""
        normal = _TMP_DIR / "concurrente-normal.jpg"
        bomba = _TMP_DIR / "concurrente-65mp.png"
        Image.new("RGB", (20, 20), "blue").save(normal, format="JPEG")
        Image.new("1", (10000, 6501)).save(bomba, format="PNG")

        abrir_real = Image.open
        normal_abierta = threading.Event()
        bomba_abierta = threading.Event()
        liberar_normal = threading.Event()
        liberar_bomba = threading.Event()

        def abrir_coordinado(path, *args, **kwargs):
            resultado = abrir_real(path, *args, **kwargs)
            if Path(path) == normal and not normal_abierta.is_set():
                normal_abierta.set()
                liberar_normal.wait(timeout=5)
            elif Path(path) == bomba and not bomba_abierta.is_set():
                bomba_abierta.set()
                liberar_bomba.wait(timeout=5)
            return resultado

        errores: dict[str, Exception] = {}

        def validar(nombre: str, path: Path):
            try:
                validate_image(path)
            except Exception as exc:  # se comprueba el tipo exacto abajo
                errores[nombre] = exc

        with patch("app.media.image_validation.Image.open", side_effect=abrir_coordinado):
            hilo_normal = threading.Thread(target=validar, args=("normal", normal))
            hilo_bomba = threading.Thread(target=validar, args=("bomba", bomba))
            hilo_normal.start()
            self.assertTrue(normal_abierta.wait(timeout=5))
            hilo_bomba.start()
            self.assertTrue(bomba_abierta.wait(timeout=5))
            liberar_normal.set()
            hilo_normal.join(timeout=5)
            liberar_bomba.set()
            hilo_bomba.join(timeout=5)

        self.assertFalse(hilo_normal.is_alive())
        self.assertFalse(hilo_bomba.is_alive())
        self.assertNotIn("normal", errores)
        self.assertIsInstance(errores.get("bomba"), ImageValidationError)


if __name__ == "__main__":
    unittest.main()
