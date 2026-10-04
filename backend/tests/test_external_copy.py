import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from app.media.external_copy import temporary_jpeg_for_external_service


def _imagen_ruidosa(ruta: Path, ancho: int, alto: int):
    """Ruido pseudoaleatorio: una imagen de un color plano se comprime a nada
    y no ejercitaria el bucle de reduccion."""
    imagen = Image.new("RGB", (ancho, alto))
    pixeles = imagen.load()
    valor = 7
    for y in range(alto):
        for x in range(ancho):
            valor = (valor * 1103515245 + 12345) % 2147483648
            pixeles[x, y] = (valor % 256, (valor >> 8) % 256, (valor >> 16) % 256)
    imagen.save(ruta, "JPEG", quality=98)


class CopiaTemporalTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="albumfp-test-"))

    def tearDown(self):
        for hijo in self.dir.iterdir():
            hijo.unlink(missing_ok=True)
        self.dir.rmdir()

    def test_una_foto_grande_se_reduce_por_debajo_del_limite(self):
        origen = self.dir / "grande.jpg"
        _imagen_ruidosa(origen, 1400, 1400)
        self.assertGreater(origen.stat().st_size, 300_000)
        with temporary_jpeg_for_external_service(origen, self.dir, max_bytes=120_000) as copia:
            self.assertLessEqual(copia.stat().st_size, 120_000)

    def test_el_original_no_se_toca(self):
        origen = self.dir / "intacta.jpg"
        _imagen_ruidosa(origen, 700, 700)
        antes = origen.read_bytes()
        with temporary_jpeg_for_external_service(origen, self.dir, max_bytes=50_000):
            pass
        self.assertEqual(antes, origen.read_bytes())

    def test_la_copia_se_borra_al_salir(self):
        origen = self.dir / "borrar.jpg"
        _imagen_ruidosa(origen, 300, 300)
        with temporary_jpeg_for_external_service(origen, self.dir) as copia:
            ruta = copia
            self.assertTrue(ruta.exists())
        self.assertFalse(ruta.exists())

    def test_la_copia_se_borra_tambien_si_el_proveedor_falla(self):
        origen = self.dir / "fallo.jpg"
        _imagen_ruidosa(origen, 300, 300)
        ruta = None
        with self.assertRaises(RuntimeError):
            with temporary_jpeg_for_external_service(origen, self.dir) as copia:
                ruta = copia
                raise RuntimeError("el proveedor se cayó")
        self.assertFalse(ruta.exists())

    def test_un_png_con_transparencia_sale_como_jpeg_valido(self):
        origen = self.dir / "alfa.png"
        Image.new("RGBA", (200, 200), (255, 0, 0, 128)).save(origen)
        with temporary_jpeg_for_external_service(origen, self.dir) as copia:
            with Image.open(copia) as convertida:
                self.assertEqual("JPEG", convertida.format)
                self.assertEqual("RGB", convertida.mode)

    def test_la_copia_no_lleva_el_exif_del_original(self):
        origen = self.dir / "con-exif.jpg"
        imagen = Image.new("RGB", (200, 200), (10, 120, 60))
        exif = imagen.getexif()
        exif[0x010F] = "AlbumFP Camera"
        imagen.save(origen, "JPEG", exif=exif)
        with Image.open(origen) as comprobar:
            self.assertTrue(dict(comprobar.getexif()))
        with temporary_jpeg_for_external_service(origen, self.dir) as copia:
            with Image.open(copia) as enviada:
                self.assertFalse(dict(enviada.getexif()))


if __name__ == "__main__":
    unittest.main()
