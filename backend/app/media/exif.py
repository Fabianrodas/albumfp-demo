"""Lectura local de los metadatos EXIF de una foto.

El archivo **no sale del servidor**: lo lee Pillow desde el disco donde ya se
guardo. Aqui no hay ninguna llamada a terceros.

Contrato duro: `extract_image_exif` **nunca lanza**. Se ejecuta despues de que
la subida ya esta escrita en disco, asi que una excepcion aqui tirararia una
subida perfectamente valida. Un archivo que no es imagen, que no existe o que
trae los metadatos corruptos devuelve todos los campos a None.

EXIF es contenido no confiable: lo escribe el dispositivo del usuario y puede
venir con basura, con textos larguisimos o con coordenadas imposibles. Por eso
todo se valida y se recorta antes de llegar a la base de datos.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PIL import Image

CAMPOS = (
    "taken_at_original", "latitude", "longitude", "altitude_m",
    "camera_make", "camera_model", "orientation",
)

# Lo que aceptan camera_make/camera_model en `media_exif`.
MAX_TEXTO = 120

# Etiquetas por numero, que es como las devuelve Pillow.
_MAKE, _MODEL, _ORIENTATION = 0x010F, 0x0110, 0x0112
_EXIF_IFD, _GPS_IFD = 0x8769, 0x8825
_DATETIME_ORIGINAL = 0x9003
_GPS_LAT_REF, _GPS_LAT, _GPS_LON_REF, _GPS_LON = 1, 2, 3, 4
_GPS_ALT_REF, _GPS_ALT = 5, 6

# Cuanto puede valer como maximo cada eje, segun su letra.
_LIMITES = {"N": 90.0, "S": 90.0, "E": 180.0, "W": 180.0}


def _clean_text(value) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        return None
    limpio = value.replace("\x00", "").strip()
    return limpio[:MAX_TEXTO] or None


def _parse_exif_datetime(value) -> datetime | None:
    texto = _clean_text(value)
    if not texto:
        return None
    try:
        # Una camara sin reloj en hora escribe "0000:00:00 00:00:00", que
        # strptime rechaza solo: mes 0 no existe.
        return datetime.strptime(texto, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def _ref(value) -> str:
    """La letra N/S/E/W, que llega como texto o como bytes segun quien escriba."""
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="replace")
    if not isinstance(value, str):
        return ""
    return value.strip().upper()[:1]


def _to_degrees(values, ref) -> float | None:
    """(grados, minutos, segundos) + N/S/E/W -> grados decimales."""
    letra = _ref(ref)
    if letra not in _LIMITES:
        return None
    try:
        grados, minutos, segundos = (float(v) for v in values)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None

    decimales = grados + minutos / 60 + segundos / 3600
    # Este `not (...)` tambien descarta el NaN que sale de un racional con
    # denominador 0, porque cualquier comparacion con NaN es falsa.
    if not 0 <= decimales <= _LIMITES[letra]:
        return None
    return -decimales if letra in ("S", "W") else decimales


def _altitude(gps: dict) -> float | None:
    bruto = gps.get(_GPS_ALT)
    if bruto is None:
        return None
    try:
        metros = float(bruto)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    # NaN, y cualquier cosa que no quepa en NUMERIC(9,2).
    if metros != metros or abs(metros) > 1_000_000:
        return None

    referencia = gps.get(_GPS_ALT_REF)
    # Llega como entero o como bytes, segun quien escribiera el EXIF.
    if isinstance(referencia, bytes):
        referencia = referencia[0] if referencia else 0
    return round(-metros if referencia == 1 else metros, 2)


def extract_image_exif(path: Path | str) -> dict:
    """Los siete campos de `media_exif`. Los que falten van a None."""
    datos = {campo: None for campo in CAMPOS}
    try:
        with Image.open(path) as imagen:
            exif = imagen.getexif()

            datos["camera_make"] = _clean_text(exif.get(_MAKE))
            datos["camera_model"] = _clean_text(exif.get(_MODEL))

            orientacion = exif.get(_ORIENTATION)
            if isinstance(orientacion, int) and 1 <= orientacion <= 8:
                datos["orientation"] = orientacion

            datos["taken_at_original"] = _parse_exif_datetime(
                exif.get_ifd(_EXIF_IFD).get(_DATETIME_ORIGINAL)
            )

            gps = exif.get_ifd(_GPS_IFD) or {}
            latitud = _to_degrees(gps.get(_GPS_LAT), gps.get(_GPS_LAT_REF))
            longitud = _to_degrees(gps.get(_GPS_LON), gps.get(_GPS_LON_REF))
            # Media coordenada no ubica nada: o van las dos o no va ninguna.
            if latitud is not None and longitud is not None:
                datos["latitude"] = round(latitud, 6)
                datos["longitude"] = round(longitud, 6)
                datos["altitude_m"] = _altitude(gps)
    except Exception:
        # A proposito tan ancho: Pillow lanza de todo ante un archivo corrupto,
        # y esto corre con la subida ya escrita en disco. Perder los metadatos
        # es aceptable; perder la foto no.
        logging.warning("No se pudieron leer los metadatos EXIF; el archivo se guarda igual")

    return datos
