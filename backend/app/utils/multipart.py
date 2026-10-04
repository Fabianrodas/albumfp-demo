"""Cuerpo `multipart/form-data` a mano, con stdlib.

Existe porque dos proveedores (OCR.Space e Imagga) quieren el archivo subido
como formulario y este backend no tiene `requests` ni ninguna otra dependencia
HTTP: las cinco integraciones anteriores van con `urllib` pelado y no vale la
pena añadir una por esto.
"""
import mimetypes
import uuid
from pathlib import Path


def build_multipart(fields: dict, file_path: Path, *, field_name: str = "file") -> tuple[bytes, str]:
    """(campos, ruta) -> (cuerpo, valor de Content-Type).

    `field_name` cambia porque cada proveedor llama distinto al suyo:
    OCR.Space espera `file` e Imagga espera `image`.
    """
    archivo = Path(file_path)
    frontera = f"----albumfp{uuid.uuid4().hex}"
    tipo = mimetypes.guess_type(archivo.name)[0] or "image/jpeg"
    partes = [
        f"--{frontera}\r\nContent-Disposition: form-data; name=\"{nombre}\"\r\n\r\n{valor}\r\n".encode("utf-8")
        for nombre, valor in fields.items()
    ]
    partes.append(
        f"--{frontera}\r\nContent-Disposition: form-data; name=\"{field_name}\"; filename=\"{archivo.name}\"\r\n"
        f"Content-Type: {tipo}\r\n\r\n".encode("utf-8")
    )
    partes.append(archivo.read_bytes())
    partes.append(f"\r\n--{frontera}--\r\n".encode("utf-8"))
    return b"".join(partes), f"multipart/form-data; boundary={frontera}"
