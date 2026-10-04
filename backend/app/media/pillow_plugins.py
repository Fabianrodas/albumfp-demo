"""Registro central de decodificadores Pillow empaquetados como ruedas pip."""
from functools import lru_cache

from pillow_heif import register_heif_opener


@lru_cache(maxsize=1)
def register_pillow_plugins() -> None:
    """Habilita HEIC/HEIF una sola vez para todos los consumidores de Pillow."""
    register_heif_opener()
