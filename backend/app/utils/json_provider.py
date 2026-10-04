"""Como el backend serializa fechas a JSON.

Todo TIMESTAMP de este esquema es "sin zona horaria" y todo datetime que
produce el codigo Python es ingenuo (sin tzinfo): la hora local de la sesion
de Postgres, o la hora que trae el EXIF de una foto o un formulario, sin
ninguna conversion de por medio.

El `DefaultJSONProvider` de Flask serializa CUALQUIER datetime con
`http_date()`, que le pone la etiqueta "GMT" aunque el valor sea una hora
local ingenua. El navegador entonces lo interpreta como un instante UTC de
verdad y lo vuelve a convertir a la zona de quien mira la pagina, asi que la
hora que se ve termina desplazada por su huso horario aunque el dato guardado
sea correcto. Se noto por primera vez con la fecha de captura EXIF, pero
afecta a cualquier fecha que devuelva la API.

Serializar un datetime ingenuo sin marca de zona (ISO simple, sin "Z" ni
desplazamiento) evita el problema: `new Date(...)` en el navegador interpreta
esa forma como hora local sin convertir nada, que es exactamente lo que el
valor representa.
"""
from datetime import datetime

from flask.json.provider import DefaultJSONProvider


class NaiveDatetimeJSONProvider(DefaultJSONProvider):
    @staticmethod
    def default(o):
        if isinstance(o, datetime) and o.tzinfo is None:
            return o.isoformat()
        return DefaultJSONProvider.default(o)
