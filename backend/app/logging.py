import logging
import re
import time
from flask import request, g
from .security.sessions import optional_user_id

# El token de un enlace publico viaja en la RUTA, no en una cabecera ni en el
# cuerpo: `/api/shared/<token>/media/7/preview`. Registrar `request.path` tal
# cual dejaria enlaces vivos a albumes privados, en claro, en el journal del
# servidor -- exactamente lo que S08 evito en la base de datos guardando solo
# `sha256(token)`. Quien pudiera leer el journal abriria cualquiera de ellos.
#
# Se sustituye SOLO el segmento del token; el resto de la ruta se conserva para
# que el log siga sirviendo para depurar. Nada fuera de `/shared/` se toca, y en
# particular `/auth/login` queda intacto, que es de donde lee el filtro de
# Fail2ban (`deployment/fail2ban/filter.d/albumfp-auth.conf`).
_SHARE_TOKEN_IN_PATH = re.compile(r"(/shared/)[^/]+")


def redact_path(path: str) -> str:
    """Devuelve `path` sin el token de un enlace publico."""
    return _SHARE_TOKEN_IN_PATH.sub(r"<token>", path)


def setup_logging(app):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )

    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    @app.before_request
    def _start_timer():
        g._start_time = time.time()

    @app.after_request
    def _log_request(response):
        try:
            duration_ms = int((time.time() - g._start_time) * 1000)
        except Exception:
            duration_ms = -1

        # `session_required` ya dejo la identidad en `g` si la ruta estaba
        # autenticada. En una ruta publica no hay ninguna, y NO se va a
        # buscar: resolver la sesion aqui costaria una consulta extra por
        # peticion solo para el log.
        user_id = optional_user_id()

        logging.info(
            "%s %s %s | %sms | ip=%s | user=%s",
            request.method,
            redact_path(request.path),
            response.status_code,
            duration_ms,
            request.remote_addr,
            user_id,
        )
        return response