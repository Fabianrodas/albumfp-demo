"""Validacion profunda de imagenes con Pillow -- fase S06 del roadmap de
seguridad. La firma de bytes (`detect_media_signature`) solo comprueba que
la cabecera parezca la de un JPEG/PNG/etc.; un archivo puede pasar esa
comprobacion y seguir siendo un decompression bomb (dimensiones absurdas
detras de una cabecera pequeña) o estar corrupto de una forma que solo se
nota al decodificar los pixeles de verdad. Esto corre DESPUES de cuarentena
y ANTES de promover -- mismo punto que dejo preparado S05.

S06 tambien incluia un escaneo de malware con ClamAV, y S07 anadio
validacion de video con ffprobe; los dos se retiraron el mismo dia porque
ambos exigen un binario/daemon instalado a nivel de sistema operativo, algo
que no viaja con `pip install` y que podria no estar disponible en un host
cloud gestionado (ver la nota de portabilidad en CLAUDE.md). Esta
decodificacion con Pillow SI se conservo: es una libreria Python normal,
funciona igual en cualquier host sin nada que instalar aparte.

Pillow valida la decodificacion de imagenes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .pillow_plugins import register_pillow_plugins

register_pillow_plugins()

# Limite EXPLICITO de megapixeles totales (no de bytes de archivo -- un
# archivo pequeño puede desempacar a miles de millones de pixeles, la
# "decompression bomb" clasica). Mas bajo que el default de Pillow
# (89 478 485) a proposito: una foto real de camara rara vez pasa de 50MP, y
# fijarlo aqui explicito no depende de que el default de la libreria no
# cambie entre versiones.
MAX_IMAGE_PIXELS = 64_000_000

_FORMAT_TO_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
    "AVIF": "image/avif",
    "HEIF": "image/heic",
}


class ImageValidationError(Exception):
    """El archivo no es una imagen valida y decodificable, o supera el
    limite de pixeles permitido."""


@dataclass(frozen=True)
class ValidatedImage:
    format: str
    mime_type: str
    width: int
    height: int


def validate_image(path: Path) -> ValidatedImage:
    """Decodifica el archivo DOS VECES: `verify()` primero (rapido, detecta
    corrupcion estructural sin decodificar los pixeles), y una segunda
    apertura para leer los pixeles de verdad y las dimensiones reales --
    `verify()` deja el objeto inservible para cualquier otra cosa, ni
    siquiera se le puede leer `.size` despues.

    Nunca devuelve una imagen "casi valida": cualquier corrupcion, formato
    no decodificable, o exceso de pixeles termina en `ImageValidationError`.
    """
    try:
        with Image.open(path) as imagen:
            ancho, alto = imagen.size
            if ancho * alto > MAX_IMAGE_PIXELS:
                raise ImageValidationError("La imagen supera el límite de píxeles permitido")
            imagen.verify()
    except ImageValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageValidationError("La imagen supera el límite de píxeles permitido") from exc
    except Exception as exc:
        raise ImageValidationError("El archivo no es una imagen válida") from exc

    try:
        with Image.open(path) as imagen:
            ancho, alto = imagen.size
            if ancho * alto > MAX_IMAGE_PIXELS:
                raise ImageValidationError("La imagen supera el límite de píxeles permitido")
            imagen.load()
            formato = (imagen.format or "").upper()
    except ImageValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageValidationError("La imagen supera el límite de píxeles permitido") from exc
    except Exception as exc:
        raise ImageValidationError("El archivo no es una imagen válida") from exc

    if ancho <= 0 or alto <= 0:
        raise ImageValidationError("El archivo no es una imagen válida")

    return ValidatedImage(
        format=formato,
        mime_type=_FORMAT_TO_MIME.get(formato, "application/octet-stream"),
        width=ancho,
        height=alto,
    )
