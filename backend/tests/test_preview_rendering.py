"""render_image_preview: solo renderiza, no persiste (spec §12)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from pillow_heif import from_pillow

from app.media.previews import LocalPreview, render_image_preview


def imagen(destino: Path, ancho: int, alto: int) -> Path:
    Image.new("RGB", (ancho, alto), (120, 30, 60)).save(destino, "JPEG", quality=95)
    return destino


def imagen_ya_optimizada(destino: Path) -> Path:
    """Ruido pseudoaleatorio ya en WebP muy comprimido: un color plano se
    comprime a casi nada en cualquier formato y no distinguiria si
    recomprimir de verdad no gana nada (comprobado: una JPEG de color plano
    SIEMPRE sale mas chica al recomprimirse a WebP, asi que no sirve para
    este caso). El ruido si defiende el tamano -- y a mas calidad (82 aqui
    contra 20 del origen) le cuesta mas bytes, no menos."""
    imagen = Image.new("RGB", (60, 45))
    pixeles = imagen.load()
    valor = 7
    for y in range(45):
        for x in range(60):
            valor = (valor * 1103515245 + 12345) % 2147483648
            pixeles[x, y] = (valor % 256, (valor >> 8) % 256, (valor >> 16) % 256)
    imagen.save(destino, "WEBP", quality=20)
    return destino


class RenderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.salida = Path(self._tmp.name) / "salida"
        self.salida.mkdir()

    def test_devuelve_un_local_preview_con_el_archivo_en_el_directorio_pedido(self):
        origen = imagen(Path(self._tmp.name) / "foto.jpg", 3000, 2000)
        resultado = render_image_preview(origen, self.salida)
        self.assertIsInstance(resultado, LocalPreview)
        self.assertEqual(self.salida, resultado.path.parent)
        self.assertTrue(resultado.path.is_file())
        self.assertEqual("image/webp", resultado.mime_type)

    def test_reduce_al_lado_mayor_conservando_proporcion(self):
        origen = imagen(Path(self._tmp.name) / "foto.jpg", 3000, 2000)
        resultado = render_image_preview(origen, self.salida)
        self.assertEqual(1280, resultado.width)
        self.assertEqual(853, resultado.height)

    def test_el_tamano_del_archivo_coincide_con_el_declarado(self):
        origen = imagen(Path(self._tmp.name) / "foto.jpg", 3000, 2000)
        resultado = render_image_preview(origen, self.salida)
        self.assertEqual(resultado.path.stat().st_size, resultado.file_size)

    def test_no_toca_el_original(self):
        origen = imagen(Path(self._tmp.name) / "foto.jpg", 2000, 1500)
        antes = origen.read_bytes()
        render_image_preview(origen, self.salida)
        self.assertEqual(antes, origen.read_bytes())

    def test_heic_genera_webp_sin_tocar_el_original(self):
        origen = Path(self._tmp.name) / "iphone.heic"
        # Ruido grande + reduccion a 1280: evita que el guard de tamano
        # descarte la preview y prueba el decoder real, no un mock.
        imagen_heic = Image.effect_noise((1800, 1400), 80).convert("RGB")
        exif = imagen_heic.getexif()
        exif[271] = "Synthetic Camera"
        from_pillow(imagen_heic).save(origen, quality=95, exif=exif.tobytes())
        original = origen.read_bytes()

        resultado = render_image_preview(origen, self.salida)

        self.assertIsNotNone(resultado)
        self.assertEqual("image/webp", resultado.mime_type)
        self.assertEqual(original, origen.read_bytes())
        self.assertLess(resultado.file_size, len(original))
        with Image.open(resultado.path) as preview:
            self.assertEqual("WEBP", preview.format)
            self.assertFalse(preview.getexif())

    def test_descarta_la_vista_previa_que_no_pesa_menos_que_el_original(self):
        # Una imagen ya pequeña y muy comprimida: recomprimir a WebP no gana nada.
        origen = imagen_ya_optimizada(Path(self._tmp.name) / "chica.webp")
        self.assertIsNone(render_image_preview(origen, self.salida))

    def test_no_deja_residuos_cuando_descarta(self):
        origen = imagen_ya_optimizada(Path(self._tmp.name) / "chica.webp")
        render_image_preview(origen, self.salida)
        self.assertEqual([], list(self.salida.iterdir()))

    def test_un_archivo_que_no_es_imagen_devuelve_none_sin_lanzar(self):
        roto = Path(self._tmp.name) / "roto.jpg"
        roto.write_bytes(b"no soy una imagen")
        self.assertIsNone(render_image_preview(roto, self.salida))

    def test_un_archivo_inexistente_devuelve_none_sin_lanzar(self):
        self.assertIsNone(render_image_preview(Path(self._tmp.name) / "no-existe.jpg", self.salida))

    def test_rechaza_mas_de_64_mp_antes_de_decodificar_pixeles(self):
        origen = Path(self._tmp.name) / "enorme.jpg"
        origen.write_bytes(b"fixture")
        abierta = MagicMock()
        abierta.__enter__.return_value = abierta
        abierta.__exit__.return_value = False
        abierta.size = (10000, 6501)

        with patch("app.media.previews.Image.open", return_value=abierta):
            self.assertIsNone(render_image_preview(origen, self.salida))

        abierta.load.assert_not_called()

    def test_la_vista_previa_no_lleva_exif(self):
        from PIL import Image as PILImage

        origen = imagen(Path(self._tmp.name) / "foto.jpg", 2000, 1500)
        resultado = render_image_preview(origen, self.salida)
        with PILImage.open(resultado.path) as abierta:
            self.assertFalse(abierta.getexif())

    def test_aplica_la_orientacion_exif_a_los_pixeles_antes_de_guardar(self):
        # Los pixeles guardados son apaisados (100x50), pero Orientation=6 dice
        # que hay que rotar 90 grados para verla derecha: la vista previa debe
        # salir con las dimensiones YA giradas (50x100), no las del archivo tal
        # cual esta en disco. Sin exif_transpose() saldria 100x50.
        origen = Path(self._tmp.name) / "girada.jpg"
        base = Image.new("RGB", (100, 50), (10, 200, 30))
        exif = base.getexif()
        exif[274] = 6  # Orientation
        base.save(origen, "JPEG", exif=exif)

        resultado = render_image_preview(origen, self.salida)
        self.assertEqual(50, resultado.width)
        self.assertEqual(100, resultado.height)

    def test_no_hace_ninguna_llamada_de_almacenamiento(self):
        fuente = Path(__file__).resolve().parents[1] / "app" / "media" / "previews.py"
        cuerpo = fuente.read_text(encoding="utf-8")
        inicio = cuerpo.index("def render_image_preview")
        fin = cuerpo.index("def create_image_preview")
        self.assertNotIn("put_from_path", cuerpo[inicio:fin])
        self.assertNotIn("get_storage_backend", cuerpo[inicio:fin])


if __name__ == "__main__":
    unittest.main()
