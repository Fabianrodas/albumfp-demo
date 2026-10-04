"""Copia temporal y reducida de una foto, para mandarla a un servicio externo.

El archivo original **nunca se toca**: se abre en solo lectura y todo lo que
se recorta, reescala o recomprime ocurre sobre un archivo temporal aparte que
se borra siempre al salir del `with`, tambien si el proveedor falla.

Existe porque el nivel gratuito de OCR.Space acepta como mucho 1 MB por
imagen, y una foto de telefono pesa varias veces eso. Reescalar la copia (no
el original) es la unica forma de que una foto normal quepa.

De regalo, guardar con Pillow **no copia el EXIF**: la imagen que sale hacia
el proveedor va sin GPS, sin camara y sin fecha. Es exactamente lo que se
quiere mandar fuera -- los pixeles y nada mas.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from pathlib import Path

from PIL import Image

# De mayor a menor. Se prueba primero bajar calidad y solo despues encoger,
# porque el texto sobrevive mejor a un JPEG mas comprimido que a perder
# pixeles, y aqui lo unico que importa es que el texto siga siendo legible.
ANCHOS = (2400, 1800, 1400, 1000, 700)
CALIDADES = (85, 70, 55, 40)


@contextmanager
def temporary_jpeg_for_external_service(source: Path, output_dir: Path, max_bytes: int = 950_000):
    """Cede la ruta de un JPEG temporal de como mucho `max_bytes`, dentro del
    `output_dir` que indique el llamador (un workspace gestionado -- spec
    §11 --, nunca un directorio suelto de `tempfile`).

    Si ni con la combinacion mas agresiva se baja del limite, cede igual la
    mas pequeña que se pudo producir: quien llame decide si la manda o no.
    """
    destino = Path(output_dir) / f"{uuid.uuid4().hex}.jpg"
    try:
        with Image.open(source) as abierta:
            abierta.load()
            # JPEG no admite alfa ni paletas; convertir siempre evita tener
            # que distinguir PNG con transparencia, GIF, CMYK...
            imagen = abierta.convert("RGB")

        for ancho in ANCHOS:
            copia = imagen
            if imagen.width > ancho:
                alto = max(1, round(imagen.height * ancho / imagen.width))
                copia = imagen.resize((ancho, alto), Image.LANCZOS)
            for calidad in CALIDADES:
                copia.save(destino, "JPEG", quality=calidad, optimize=True)
                if destino.stat().st_size <= max_bytes:
                    yield destino
                    return
        yield destino
    finally:
        destino.unlink(missing_ok=True)
