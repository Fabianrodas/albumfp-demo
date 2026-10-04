import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

import app.storage.media_storage as media_storage
from app.media.previews import MAX_DIMENSION, create_image_preview, create_preview_for_stored


def _imagen(ruta: Path, ancho: int, alto: int, formato: str = "JPEG", **guardar):
    """Ruido pseudoaleatorio: un color plano se comprime a nada y no dejaria
    ver si el reescalado hizo algo."""
    imagen = Image.new("RGB", (ancho, alto))
    pixeles = imagen.load()
    valor = 7
    for y in range(alto):
        for x in range(ancho):
            valor = (valor * 1103515245 + 12345) % 2147483648
            pixeles[x, y] = (valor % 256, (valor >> 8) % 256, (valor >> 16) % 256)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    imagen.save(ruta, formato, **guardar)


class VistaPreviaTests(unittest.TestCase):
    def setUp(self):
        self.raiz = Path(tempfile.mkdtemp(prefix="albumfp-prev-"))
        self._anterior = media_storage._STORAGE_ROOT
        media_storage._STORAGE_ROOT = self.raiz

    def tearDown(self):
        media_storage._STORAGE_ROOT = self._anterior
        shutil.rmtree(self.raiz, ignore_errors=True)

    def _original(self, nombre="user_1/album_1/foto.jpg", ancho=2400, alto=1600, **kw):
        ruta = self.raiz / nombre
        _imagen(ruta, ancho, alto, **kw)
        return ruta

    def test_reduce_al_lado_mayor_y_conserva_proporcion(self):
        origen = self._original(ancho=2400, alto=1600)
        preview = create_image_preview(origen, Path("user_1/album_1"))
        self.assertIsNotNone(preview)
        self.assertEqual(MAX_DIMENSION, preview["width"])
        # 2400x1600 es 3:2; a 1280 de ancho tocan 853 de alto.
        self.assertEqual(853, preview["height"])
        self.assertLessEqual(max(preview["width"], preview["height"]), MAX_DIMENSION)

    def test_una_foto_vertical_tambien_se_corta_por_el_lado_mayor(self):
        origen = self._original(ancho=1600, alto=2400)
        preview = create_image_preview(origen, Path("user_1/album_1"))
        self.assertEqual(MAX_DIMENSION, preview["height"])
        self.assertLessEqual(preview["width"], MAX_DIMENSION)

    def test_no_agranda_una_foto_ya_pequena(self):
        # PNG a proposito: un PNG de ruido pesa muchisimo, asi que el WebP sale
        # mas pequeño y la vista previa se conserva. Con un JPEG pequeño se
        # descartaria por el guardia de tamaño y no se veria lo que mide aqui,
        # que es que las dimensiones NO crecen hasta MAX_DIMENSION.
        origen = self.raiz / "user_1/album_1/pequena.png"
        _imagen(origen, 400, 300, "PNG")
        preview = create_image_preview(origen, Path("user_1/album_1"))
        self.assertIsNotNone(preview)
        self.assertEqual((400, 300), (preview["width"], preview["height"]))

    def test_el_original_no_se_toca(self):
        origen = self._original(ancho=2000, alto=2000)
        antes = origen.read_bytes()
        create_image_preview(origen, Path("user_1/album_1"))
        self.assertEqual(antes, origen.read_bytes())

    def test_la_vista_previa_es_webp_y_pesa_menos(self):
        origen = self._original(ancho=2400, alto=1600)
        preview = create_image_preview(origen, Path("user_1/album_1"))
        self.assertEqual("image/webp", preview["mime_type"])
        self.assertTrue(preview["storage_path"].endswith(".webp"))
        destino = self.raiz / preview["storage_path"]
        with Image.open(destino) as abierta:
            self.assertEqual("WEBP", abierta.format)
        self.assertLess(destino.stat().st_size, origen.stat().st_size)

    def test_el_resultado_trae_su_propio_tamano(self):
        # S03: hace falta persistido para calcular cuota de almacenamiento
        # sin un stat() por archivo en cada comprobacion.
        origen = self._original(ancho=2400, alto=1600)
        preview = create_image_preview(origen, Path("user_1/album_1"))
        destino = self.raiz / preview["storage_path"]
        self.assertEqual(destino.stat().st_size, preview["file_size"])

    def test_se_guarda_junto_al_original_y_no_deja_archivos_a_medias(self):
        origen = self._original()
        preview = create_image_preview(origen, Path("user_1/album_1"))
        self.assertTrue(preview["storage_path"].startswith("user_1/album_1/"))
        sobrantes = [p.name for p in (self.raiz / "user_1/album_1").iterdir() if p.name.startswith(".")]
        self.assertEqual([], sobrantes, "quedo un .part sin renombrar")

    def test_la_vista_previa_no_lleva_exif(self):
        """`Image.save()` de Pillow no copia el EXIF. Es lo que hace que una
        miniatura no arrastre GPS ni camara, asi que conviene fijarlo."""
        origen = self.raiz / "user_1/album_1/con_exif.jpg"
        origen.parent.mkdir(parents=True, exist_ok=True)
        imagen = Image.new("RGB", (2000, 1500), (120, 30, 30))
        exif = imagen.getexif()
        exif[271] = "AlbumFP Camera"   # Make
        exif[306] = "2026:01:02 03:04:05"  # DateTime
        imagen.save(origen, "JPEG", exif=exif)
        with Image.open(origen) as abierta:
            self.assertTrue(dict(abierta.getexif()), "la prueba necesita un original CON exif")

        preview = create_image_preview(origen, Path("user_1/album_1"))
        with Image.open(self.raiz / preview["storage_path"]) as copia:
            self.assertEqual({}, dict(copia.getexif()))

    def test_se_descarta_la_vista_previa_que_no_pesa_menos_que_el_original(self):
        """Encontrado con una foto real del usuario: una imagen ya pequeña no
        se reescala, y recomprimirla a WebP puede salir MAS grande. Guardarla
        gastaria disco y ancho de banda para entregar mas bytes."""
        origen = self.raiz / "user_1/album_1/ya_optimizada.webp"
        origen.parent.mkdir(parents=True, exist_ok=True)
        # Bajo MAX_DIMENSION (no hay reescalado) y ya en WebP muy comprimido.
        _imagen(origen, 900, 400, "WEBP", quality=25)

        self.assertIsNone(create_image_preview(origen, Path("user_1/album_1")))
        # Y no deja ni el .part ni el .webp descartado tirados en disco.
        sobrantes = [p.name for p in (self.raiz / "user_1/album_1").iterdir() if p != origen]
        self.assertEqual([], sobrantes)

    def test_un_archivo_que_no_es_imagen_devuelve_none_sin_lanzar(self):
        basura = self.raiz / "user_1/album_1/roto.jpg"
        basura.parent.mkdir(parents=True, exist_ok=True)
        basura.write_bytes(b"esto no es una imagen")
        self.assertIsNone(create_image_preview(basura, Path("user_1/album_1")))

    def test_un_archivo_inexistente_devuelve_none_sin_lanzar(self):
        self.assertIsNone(create_image_preview(self.raiz / "no_existe.jpg", Path("user_1/album_1")))
        # Key canonica (nombre = uuid4().hex) que simplemente no existe: con
        # un nombre no canonico el None vendria de la gramatica, no de la
        # ausencia, y este caso dejaria de medir lo que dice medir.
        self.assertIsNone(create_preview_for_stored(f"user_1/album_1/{'0' * 32}.jpg"))

    def test_create_preview_for_stored_rechaza_una_ruta_que_se_sale(self):
        self.assertIsNone(create_preview_for_stored("../../fuera.jpg"))

    def test_create_preview_for_stored_guarda_junto_al_original(self):
        clave = f"user_9/album_3/{'a' * 32}.jpg"
        self._original(clave, 1800, 1200)
        preview = create_preview_for_stored(clave)
        self.assertIsNotNone(preview)
        self.assertTrue(preview["storage_path"].startswith("user_9/album_3/"))
        self.assertTrue((self.raiz / preview["storage_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
