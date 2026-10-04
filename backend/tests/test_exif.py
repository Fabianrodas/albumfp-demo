import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from app.media.exif import extract_image_exif, _to_degrees, _parse_exif_datetime, _clean_text

# 2 grados 10' 55.44" S / 79 grados 52' 48.12" W -> Guayaquil.
LAT = (IFDRational(2, 1), IFDRational(10, 1), IFDRational(5544, 100))
LON = (IFDRational(79, 1), IFDRational(52, 1), IFDRational(4812, 100))


def jpeg_con_exif(destino: Path, *, fecha=None, gps=False, camara=True, orientacion=None):
    exif = Image.Exif()
    if camara:
        exif[0x010F] = "Apple"
        exif[0x0110] = "iPhone 13"
    if orientacion is not None:
        exif[0x0112] = orientacion
    if fecha:
        exif[0x8769] = {0x9003: fecha}
    if gps:
        exif[0x8825] = {
            1: "S", 2: LAT,
            3: "W", 4: LON,
            5: 0, 6: IFDRational(1234, 100),
        }
    Image.new("RGB", (4, 4), "green").save(destino, format="JPEG", exif=exif)
    return destino


class ConversionGpsTests(unittest.TestCase):
    """El plan pide probar los ayudantes de GPS por separado."""

    def test_norte_y_este_son_positivos(self):
        self.assertAlmostEqual(2.182067, _to_degrees(LAT, "N"), places=5)
        self.assertAlmostEqual(79.880033, _to_degrees(LON, "E"), places=5)

    def test_sur_y_oeste_son_negativos(self):
        self.assertAlmostEqual(-2.182067, _to_degrees(LAT, "S"), places=5)
        self.assertAlmostEqual(-79.880033, _to_degrees(LON, "W"), places=5)

    def test_la_referencia_puede_venir_en_bytes_o_en_minuscula(self):
        self.assertLess(_to_degrees(LAT, b"S"), 0)
        self.assertLess(_to_degrees(LAT, "s"), 0)

    def test_un_racional_con_denominador_cero_no_revienta(self):
        self.assertIsNone(_to_degrees((IFDRational(1, 0), IFDRational(0, 1), IFDRational(0, 1)), "N"))

    def test_valores_que_no_son_numeros_devuelven_none(self):
        self.assertIsNone(_to_degrees(("dos", "diez", "cinco"), "N"))
        self.assertIsNone(_to_degrees(None, "N"))
        self.assertIsNone(_to_degrees((1, 2), "N"))

    def test_una_coordenada_fuera_de_rango_se_descarta(self):
        # EXIF es contenido no confiable: 200 grados no es una latitud, y dejarla
        # pasar contaminaria la fase de geocodificacion.
        self.assertIsNone(_to_degrees((IFDRational(200, 1), IFDRational(0, 1), IFDRational(0, 1)), "N"))


class FechaYTextoTests(unittest.TestCase):
    def test_el_formato_exif_se_convierte_a_datetime(self):
        self.assertEqual(datetime(2021, 12, 19, 18, 18, 3), _parse_exif_datetime("2021:12:19 18:18:03"))

    def test_una_fecha_a_ceros_no_es_una_fecha(self):
        # Muchas camaras escriben esto cuando no tienen reloj en hora.
        self.assertIsNone(_parse_exif_datetime("0000:00:00 00:00:00"))

    def test_una_fecha_ilegible_devuelve_none(self):
        for basura in ("", None, "ayer", "2021-12-19", 12345):
            self.assertIsNone(_parse_exif_datetime(basura))

    def test_el_texto_pierde_nulos_y_espacios(self):
        self.assertEqual("Apple", _clean_text("  Apple\x00\x00 "))
        self.assertIsNone(_clean_text("   "))
        self.assertIsNone(_clean_text(None))

    def test_el_texto_se_recorta_para_caber_en_la_columna(self):
        self.assertEqual(120, len(_clean_text("A" * 300)))


class ExtraccionCompletaTests(unittest.TestCase):
    def test_una_foto_con_fecha_camara_y_gps(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = jpeg_con_exif(Path(tmp) / "foto.jpg", fecha="2021:12:19 18:18:03", gps=True, orientacion=1)
            datos = extract_image_exif(ruta)

        self.assertEqual(datetime(2021, 12, 19, 18, 18, 3), datos["taken_at_original"])
        self.assertAlmostEqual(-2.182067, datos["latitude"], places=5)
        self.assertAlmostEqual(-79.880033, datos["longitude"], places=5)
        self.assertAlmostEqual(12.34, float(datos["altitude_m"]), places=2)
        self.assertEqual("Apple", datos["camera_make"])
        self.assertEqual("iPhone 13", datos["camera_model"])
        self.assertEqual(1, datos["orientation"])

    def test_una_foto_sin_exif_no_falla(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "plano.jpg"
            Image.new("RGB", (4, 4), "blue").save(ruta, format="JPEG")
            datos = extract_image_exif(ruta)

        self.assertEqual(7, len(datos))
        self.assertTrue(all(valor is None for valor in datos.values()), datos)

    def test_un_png_sin_exif_no_falla(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "plano.png"
            Image.new("RGB", (4, 4), "blue").save(ruta, format="PNG")
            self.assertTrue(all(valor is None for valor in extract_image_exif(ruta).values()))

    def test_un_archivo_que_no_es_imagen_no_rompe_la_subida(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "video.mp4"
            ruta.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
            self.assertTrue(all(valor is None for valor in extract_image_exif(ruta).values()))

    def test_un_archivo_corrupto_no_rompe_la_subida(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "roto.jpg"
            ruta.write_bytes(b"\xff\xd8\xff\xe0" + b"basura" * 40)
            self.assertTrue(all(valor is None for valor in extract_image_exif(ruta).values()))

    def test_un_archivo_que_no_existe_no_rompe_la_subida(self):
        self.assertTrue(all(valor is None for valor in extract_image_exif(Path("no-existe-nunca.jpg")).values()))

    def test_gps_incompleto_no_inventa_coordenadas(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "media.jpg"
            exif = Image.Exif()
            exif[0x8825] = {2: LAT}  # latitud sin referencia ni longitud
            Image.new("RGB", (4, 4), "red").save(ruta, format="JPEG", exif=exif)
            datos = extract_image_exif(ruta)

        self.assertIsNone(datos["longitude"])


if __name__ == "__main__":
    unittest.main()
