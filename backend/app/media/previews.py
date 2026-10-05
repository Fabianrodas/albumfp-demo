"""Vista previa WebP de una foto: renderizado y persistencia son dos pasos.

Existe porque la cuadricula pedia el ORIGINAL de cada foto para pintar una
tarjeta de unos cientos de pixeles: una foto de telefono son varios MB y una
cuadricula son doce. La vista previa es la misma imagen a 1280px como mucho,
en WebP, guardada junto al original y protegida igual que el.

**Renderizar y persistir son pasos separados (spec de origen privado, S12).**
`render_image_preview()` solo decodifica, reescala y comprime -- nunca toca
almacenamiento -- y deja el archivo en el directorio que le pida quien llama.
En local ese directorio es un workspace gestionado y efimero (`local_workspace`
en `app/storage/workspace.py`), nunca un `/tmp` suelto; en remote sera el
mismo punto por el que la subida ya materializa/renderiza antes de mandar el
PUT al origin. Quien decide DONDE queda la vista previa final -- el backend de
almacenamiento seleccionado -- es `create_image_preview()`, que compone sobre
`render_image_preview()` y sobre `get_storage_backend().put_from_path()`.

**El original nunca se toca**: se abre en solo lectura y todo lo que se
reescala o recomprime va a un archivo aparte. La escritura atomica (`.part` +
reemplazo) ya no es cosa de este modulo: la hace el backend de almacenamiento
al persistir, igual que con cualquier otro objeto.

**No lanza nunca, y eso es el contrato, no una precaucion.** Corre justo
despues de que `save_upload` dejo el original en disco, asi que una excepcion
aqui tiraria una subida perfectamente valida. Un archivo corrupto, que no es
imagen, un formato que Pillow no sepa escribir o un fallo al persistir
devuelven None y la foto se sube igual -- sin vista previa, cayendo al
original al servirla. Mismo criterio que `extract_image_exif` y
`_try_auto_enrich`.

**La vista previa va sin EXIF**, gratis: `Image.save()` de Pillow no lo copia.
Lo que si se conserva es la ORIENTACION, aplicandola a los pixeles con
`exif_transpose` antes de guardar -- si no, una foto vertical de telefono
(que se ve derecha solo porque su EXIF dice "girala") saldria tumbada en la
cuadricula, que es justo donde ya no queda EXIF que interpretar.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from .pillow_plugins import register_pillow_plugins
from .image_validation import MAX_IMAGE_PIXELS

register_pillow_plugins()

# Suficiente para la tarjeta mas grande de la cuadricula y para una pantalla
# de mucha densidad; mas alla de esto ya se pide el original.
MAX_DIMENSION = 1280
QUALITY = 82
MIME_TYPE = "image/webp"


@dataclass(frozen=True)
class LocalPreview:
    """Vista previa ya renderizada en disco, todavia sin persistir."""

    path: Path
    width: int
    height: int
    mime_type: str
    file_size: int


def render_image_preview(source_path: Path, output_dir: Path, *, keep_larger: bool = False) -> LocalPreview | None:
    """Renderiza la vista previa de `source_path` dentro de `output_dir`.

    Sin efectos sobre el almacenamiento final: `output_dir` es responsabilidad
    de quien llama (un workspace gestionado, en la practica), y esta funcion
    no sabe -- ni necesita saber -- donde acabara viviendo el resultado.
    Nunca lanza; un archivo corrupto, que no es imagen, o una vista previa que
    no pesa menos que el original devuelven None sin dejar residuos en
    `output_dir`.

    `keep_larger` (v1.1, portadas de video): la portada NO tiene un original
    de imagen al que caer, así que se conserva aunque no pese menos que el
    fotograma que mandó el navegador.
    """
    destino = Path(output_dir) / f"{uuid.uuid4().hex}.webp"
    try:
        with Image.open(source_path) as abierta:
            ancho, alto = abierta.size
            if ancho * alto > MAX_IMAGE_PIXELS:
                return None
            abierta.load()
            # Aplica la orientacion del EXIF a los pixeles y la descarta: la
            # copia que sale de aqui no lleva EXIF que la vuelva a girar.
            imagen = ImageOps.exif_transpose(abierta)
            # WebP no admite paletas ni CMYK. RGBA se conserva (PNG con
            # transparencia); cualquier otra cosa baja a RGB.
            if imagen.mode not in ("RGB", "RGBA"):
                imagen = imagen.convert("RGBA" if "A" in imagen.getbands() else "RGB")

            # Nunca agrandar: una imagen ya pequeña se copia tal cual, y
            # estirarla solo gastaria bytes sin añadir un pixel de detalle.
            if max(imagen.size) > MAX_DIMENSION:
                imagen = imagen.copy()
                imagen.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

            imagen.save(destino, "WEBP", quality=QUALITY, method=4)

            # Una vista previa que no pesa menos que el original no sirve de
            # nada: gastaria disco Y ancho de banda para entregar MAS bytes.
            # Pasa de verdad con imagenes ya pequeñas o ya muy optimizadas,
            # donde no hubo reescalado y recomprimir a WebP no gana nada.
            tamano = destino.stat().st_size
            if not keep_larger and tamano >= Path(source_path).stat().st_size:
                destino.unlink(missing_ok=True)
                return None

            return LocalPreview(destino, imagen.width, imagen.height, MIME_TYPE, tamano)
    except Exception:
        logging.warning("No se pudo generar la vista previa; se servira el original", exc_info=False)
        destino.unlink(missing_ok=True)
        return None


def create_image_preview(original_path: Path, relative_dir: Path) -> dict | None:
    """Renderiza la vista previa de `original_path` y la persiste bajo el
    mismo `user_<id>/<scope>` que el original, con una key nueva generada por
    el servidor.

    Wrapper de conveniencia para media YA EXISTENTE (lo usa el backfill): la
    subida en curso, que ya trae su propio workspace y su propia transaccion,
    llama a `render_image_preview()` directamente en vez de este wrapper.

    Devuelve `{"storage_path", "mime_type", "width", "height", "file_size"}`
    o None si no se pudo generar o persistir. Nunca lanza.
    """
    from ..storage.backends import get_storage_backend
    from ..storage.object_keys import build_object_key
    from ..storage.workspace import local_workspace

    partes = Path(relative_dir).as_posix().split("/")
    if len(partes) != 2 or not partes[0].startswith("user_"):
        return None
    try:
        owner_id = int(partes[0].removeprefix("user_"))
    except ValueError:
        return None

    try:
        with local_workspace("upload-preview") as espacio:
            renderizada = render_image_preview(Path(original_path), espacio.directory)
            if renderizada is None:
                return None
            recibo = get_storage_backend().put_from_path(
                build_object_key(owner_id, partes[1], "webp"), renderizada.path
            )
            return {
                "storage_path": recibo.key,
                "mime_type": renderizada.mime_type,
                "width": renderizada.width,
                "height": renderizada.height,
                "file_size": renderizada.file_size,
            }
    except Exception:
        # Contrato historico: generar una vista previa nunca puede tumbar una
        # subida (o un backfill) valido. Un fallo al persistir se registra y
        # se sirve el original, igual que un fallo al renderizar.
        logging.warning("No se pudo persistir la vista previa", exc_info=False)
        return None


def create_preview_for_stored(storage_path: str) -> dict | None:
    """Igual, pero partiendo de la key relativa que guarda `media`.

    Lo usan la subida y el script de backfill: los dos tienen la key
    relativa, no un Path local, y los dos quieren la vista previa junto al
    original. Ausencia real del objeto (`ObjectNotFound`) es un None mas;
    cualquier otro fallo de almacenamiento al materializar se propaga en vez
    de tragarse aqui, porque no es lo mismo "esta foto no se puede previsualizar"
    que "el almacenamiento no responde".
    """
    from ..storage.backends import get_storage_backend
    from ..storage.contracts import ObjectNotFound
    from ..storage.object_keys import InvalidStorageKey, validate_storage_key

    try:
        clave = validate_storage_key(storage_path)
    except InvalidStorageKey:
        return None
    try:
        with get_storage_backend().materialize(clave) as origen:
            return create_image_preview(origen, Path(clave).parent)
    except ObjectNotFound:
        return None
