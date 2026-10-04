"""Sesiones opacas del lado del servidor, con cookie HttpOnly y CSRF.

Sustituye a los JWT en `localStorage`/`sessionStorage`. Lo que cambia de
verdad, y por que:

- **El navegador ya no puede leer la credencial.** Un XSS podia robar el par
  access/refresh de Web Storage y usarlo desde cualquier maquina. La cookie
  de sesion es `HttpOnly`, asi que ningun JavaScript la ve -- ni el nuestro.
- **Ahora se puede revocar de verdad.** Un JWT valido lo era hasta caducar;
  cerrar sesion solo borraba la copia del cliente. Aqui cerrar sesion marca la
  fila y la siguiente peticion falla, venga de donde venga.
- **Un dump de la base no sirve para entrar.** Se guarda solo el SHA-256 del
  token, nunca el token. Vale igual para el secreto CSRF.

El token es de 32 bytes de `secrets.token_urlsafe` (256 bits de entropia). No
se firma ni lleva datos dentro: es una llave opaca cuyo unico significado esta
en la fila que apunta.

**Las fechas son `TIMESTAMP` sin zona, no `TIMESTAMPTZ` como pedia el plan.**
Todo el esquema es ingenuo y `NaiveDatetimeJSONProvider` cuenta con ello; una
columna con zona reintroduciria el desplazamiento horario que arreglo la fase
02. La propiedad de seguridad es la misma: la caducidad la compara Postgres
contra su propio `NOW()`, nunca el navegador.
"""
import hashlib
import os
import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import g, request

from ..db.db import db_conn
from ..utils.env import int_env, truthy
from ..utils.origins import trusted_origins
from ..utils.responses import fail
from ..utils.sql_security import execute_safe

# The __Host- prefix requires a secure connection and forbids Domain.
# This loopback-only demo uses a separate cookie name for local development,
# where TLS is not configured.
SESSION_COOKIE_SECURE = "__Host-albumfp_session"
SESSION_COOKIE_DEV = "albumfp_session"
CSRF_COOKIE_SECURE = "__Host-albumfp_csrf"
CSRF_COOKIE_DEV = "albumfp_csrf"
CSRF_HEADER = "X-CSRF-Token"

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _secure_cookies() -> bool:
    """Solo se marcan `Secure` cuando la conexion de verdad es HTTPS."""
    app_env = (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "development").strip().lower()
    return app_env == "production" and truthy("HTTPS_ENABLED")


def session_cookie_name() -> str:
    return SESSION_COOKIE_SECURE if _secure_cookies() else SESSION_COOKIE_DEV


def csrf_cookie_name() -> str:
    return CSRF_COOKIE_SECURE if _secure_cookies() else CSRF_COOKIE_DEV


def hash_token(raw: str) -> str:
    """SHA-256 hex. No lleva sal a proposito: el token ya son 256 bits
    aleatorios, asi que no hay diccionario que precomputar, y la busqueda por
    igualdad exacta necesita ser indexable."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _lifetime(remember: bool) -> timedelta:
    """"Recuerdame" cambia cuanto dura la sesion, no donde se guarda.

    Sin recordar la cookie ademas no lleva `Max-Age`, asi que muere al cerrar
    el navegador; con recordar sobrevive. Los dos casos sobreviven a recargar,
    que es lo que se arreglo en su dia.
    """
    if remember:
        return timedelta(days=int_env("SESSION_REMEMBER_DAYS", 30))
    return timedelta(hours=int_env("SESSION_LIFETIME_HOURS", 24))


def summarize_user_agent(raw: str | None) -> str | None:
    """Se guarda recortado y tal cual, para que el dueño reconozca el
    dispositivo en la futura pantalla de sesiones. No se guarda la IP: es un
    dato personal que esta fase no necesita."""
    limpio = (raw or "").strip()
    return limpio[:240] or None


def create_session(conn, user_id: int, remember: bool, user_agent: str | None) -> dict:
    """Crea la fila y devuelve los DOS secretos en claro, la unica vez que
    existen fuera del navegador."""
    session_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + _lifetime(remember)

    execute_safe(
        conn,
        """
        INSERT INTO user_sessions
            (user_id, token_hash, csrf_hash, remember_me, user_agent_summary, expires_at)
        VALUES (:user_id, :token_hash, :csrf_hash, :remember_me, :user_agent, :expires_at)
        """,
        {
            "user_id": user_id,
            "token_hash": hash_token(session_token),
            "csrf_hash": hash_token(csrf_token),
            "remember_me": remember,
            "user_agent": summarize_user_agent(user_agent),
            "expires_at": expires_at,
        },
    )
    return {
        "session_token": session_token,
        "csrf_token": csrf_token,
        "expires_at": expires_at,
        "remember": remember,
    }


def load_session(conn, raw_token: str):
    """La fila viva de este token, o None.

    La cuenta desactivada se filtra aqui mismo (`u.active`), que es lo que
    hacia el `token_in_blocklist_loader` de los JWT.
    """
    if not raw_token:
        return None
    return execute_safe(
        conn,
        """
        SELECT s.id, s.user_id, s.csrf_hash, s.expires_at, s.remember_me
        FROM user_sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = :token_hash
          AND s.revoked_at IS NULL
          AND s.expires_at > NOW()
          AND u.active = TRUE
        """,
        {"token_hash": hash_token(raw_token)},
    ).mappings().first()


def touch_session(conn, session_id: int) -> None:
    execute_safe(
        conn,
        "UPDATE user_sessions SET last_used_at = NOW() WHERE id = :id",
        {"id": session_id},
    )


def revoke_session(conn, session_id: int) -> None:
    execute_safe(
        conn,
        "UPDATE user_sessions SET revoked_at = NOW() WHERE id = :id AND revoked_at IS NULL",
        {"id": session_id},
    )


def revoke_all_sessions(conn, user_id: int, keep_session_id: int | None = None) -> int:
    """Todas las sesiones de una cuenta. `keep_session_id` deja viva la actual,
    que es lo que quiere un cambio de contraseña: echar a los demas
    dispositivos sin echarte a ti del que estas usando."""
    resultado = execute_safe(
        conn,
        """
        UPDATE user_sessions
        SET revoked_at = NOW()
        WHERE user_id = :user_id
          AND revoked_at IS NULL
          AND (:keep IS NULL OR id <> :keep)
        """,
        {"user_id": user_id, "keep": keep_session_id},
    )
    return resultado.rowcount or 0


def list_sessions(conn, user_id: int):
    """Sesiones vivas de una cuenta, mas nuevas primero. Nunca selecciona
    `token_hash` ni `csrf_hash`: esta fila la va a ver el propio dueño en su
    perfil, y devolver un hash sigue siendo devolver un secreto que no hace
    falta mostrar."""
    return execute_safe(
        conn,
        """
        SELECT id, user_agent_summary, remember_me, created_at, last_used_at, expires_at
        FROM user_sessions
        WHERE user_id = :user_id
          AND revoked_at IS NULL
          AND expires_at > NOW()
        ORDER BY last_used_at DESC
        """,
        {"user_id": user_id},
    ).mappings().all()


def revoke_owned_session(conn, user_id: int, session_id: int) -> bool:
    """Revoca una sesion por id, pero SOLO si es de este usuario -- el `id`
    de sesion es un entero correlativo, adivinable, y esta es la unica
    frontera que impide que una cuenta cierre la sesion de otra."""
    resultado = execute_safe(
        conn,
        """
        UPDATE user_sessions
        SET revoked_at = NOW()
        WHERE id = :id AND user_id = :user_id AND revoked_at IS NULL
        """,
        {"id": session_id, "user_id": user_id},
    )
    return bool(resultado.rowcount)


def rotate_session(conn, old_session_id: int, user_id: int, user_agent: str | None) -> dict:
    """Revoca la fila actual y crea una nueva para el mismo usuario, con la
    misma preferencia de "Recuerdame". Se usa tras cambiar la contraseña:
    ademas de echar a los demas dispositivos, la sesion desde la que se
    cambio recibe tambien un token y un CSRF nuevos -- si el token viejo se
    hubiera filtrado (un log de proxy, por ejemplo) justo antes del cambio,
    deja de servir para nada despues."""
    fila = execute_safe(
        conn,
        "SELECT remember_me FROM user_sessions WHERE id = :id",
        {"id": old_session_id},
    ).mappings().first()
    remember = bool(fila["remember_me"]) if fila else False
    revoke_session(conn, old_session_id)
    return create_session(conn, user_id, remember, user_agent)


def _trusted_origins() -> set[str]:
    return trusted_origins()


def _origin_is_same_site() -> bool:
    """Compare mutation origins with the local trusted-origin allowlist.

    The development proxy can change the Host header Flask receives, so
    comparing Origin with request.host_url would reject valid demo requests.
    The allowlist contains only configured loopback origins. If Origin is
    absent, the CSRF header is still required.
    """
    origin = request.headers.get("Origin")
    if not origin:
        return True
    return origin.rstrip("/") in _trusted_origins()


def session_required(fn):
    """Sustituto de `@jwt_required()`.

    Ademas de autenticar, exige la cabecera CSRF en toda mutacion. Va aqui y
    no en un `before_request` suelto para que sea imposible añadir una ruta
    autenticada que escriba sin pasar por la comprobacion: quien pone el
    decorador ya la tiene.
    """
    @wraps(fn)
    def envoltura(*args, **kwargs):
        raw_token = request.cookies.get(session_cookie_name())
        with db_conn() as conn:
            sesion = load_session(conn, raw_token)
            if not sesion:
                return fail("Sesión inválida o expirada", status=401, code=401)

            if request.method not in SAFE_METHODS:
                if not _origin_is_same_site():
                    return fail("Origen no permitido", status=403, code="csrf_origin")
                enviado = request.headers.get(CSRF_HEADER) or ""
                if not enviado or not secrets.compare_digest(hash_token(enviado), sesion["csrf_hash"]):
                    return fail("Token CSRF ausente o inválido", status=403, code="csrf_invalid")

            touch_session(conn, sesion["id"])

        g.session_id = sesion["id"]
        g.user_id = sesion["user_id"]
        return fn(*args, **kwargs)

    return envoltura


def current_user_id() -> int:
    """Solo tiene sentido dentro de una vista con `@session_required`."""
    return int(g.user_id)


def current_session_id() -> int:
    return int(g.session_id)


def optional_user_id() -> int | None:
    """Para el log de peticiones, que corre tambien en rutas publicas."""
    return getattr(g, "user_id", None)


def attach_session_cookies(response, sesion: dict):
    seguro = _secure_cookies()
    # Sin recordar no se manda `max_age`: la cookie muere al cerrar el
    # navegador, que es exactamente lo que significaba el `sessionStorage` de
    # antes. Con recordar dura lo mismo que la fila.
    max_age = int(_lifetime(sesion["remember"]).total_seconds()) if sesion["remember"] else None
    comun = {"secure": seguro, "samesite": "Strict", "path": "/", "max_age": max_age}
    response.set_cookie(session_cookie_name(), sesion["session_token"], httponly=True, **comun)
    # El token CSRF SI lo lee el JavaScript de la app: tiene que poder
    # copiarlo a la cabecera. No es una credencial por si mismo -- sin la
    # cookie de sesion no autentica nada.
    response.set_cookie(csrf_cookie_name(), sesion["csrf_token"], httponly=False, **comun)
    return response


def clear_session_cookies(response):
    """Borra las CUATRO variantes de nombre, no solo la que este entorno usa:
    si alguna vez se cambio HTTPS_ENABLED, una cookie de la otra variante
    podria seguir viva en el navegador.

    Las `__Host-` necesitan `secure=True` en el propio borrado -- sin eso,
    Werkzeug manda un `Set-Cookie` de borrado SIN el atributo `Secure`, y el
    navegador lo rechaza por prefijo invalido (queda en la consola como un
    aviso de seguridad, aunque no rompe nada: esa cookie no existia en un
    entorno sin HTTPS de todas formas).
    """
    comun = {"path": "/", "samesite": "Strict"}
    for nombre in (SESSION_COOKIE_DEV, CSRF_COOKIE_DEV):
        response.delete_cookie(nombre, **comun)
    for nombre in (SESSION_COOKIE_SECURE, CSRF_COOKIE_SECURE):
        response.delete_cookie(nombre, secure=True, **comun)
    return response
