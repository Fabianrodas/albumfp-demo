import os

from dotenv import load_dotenv

# Load local development configuration before importing application modules.
# The local launcher loads the ignored Demo configuration before app modules.
load_dotenv()

from flask import Flask, request
from flask_cors import CORS

from app.utils.env import int_env, truthy as _truthy
from app.utils.json_provider import NaiveDatetimeJSONProvider
from app.utils.origins import cors_origins, trusted_origins


def create_app() -> Flask:
    # Deferred imports ensure environment configuration is already available
    # to the database and storage modules when this factory is called.
    from app.api import register_blueprints
    from app.errors import register_error_handlers
    from app.logging import setup_logging
    from app.storage.backends import validate_storage_configuration
    from app.storage.request_streams import install_budgeted_requests

    app = Flask(__name__)
    app.json = NaiveDatetimeJSONProvider(app)

    # El spool del multipart cae dentro del workspace gestionado, con
    # presupuesto y limpieza en el teardown, en vez de en /tmp. No resuelve
    # ninguna raíz al arrancar: la primera subida grande es la que las crea.
    install_budgeted_requests(app)

    origins = cors_origins()
    # Valida PUBLIC_ORIGIN durante el arranque aunque same-origin no use CORS.
    # Fallar aquí evita descubrir una configuración insegura tras un login.
    trusted_origins()
    if origins:
        CORS(
            app,
            resources={
                r"/api/*": {"origins": origins},
                r"/auth/*": {"origins": origins},
            },
        )

    # Falla al arrancar si el backend de almacenamiento está mal configurado
    # (spec S13) -- MEDIA_STORAGE_BACKEND=remote con una raíz local rellena,
    # un token demasiado corto, etc. Mejor un crash inmediato con el nombre
    # de la variable que servir tráfico con una configuración a medias
    # (Task 7 lo dejó preparado pero sin conectar; Task 29 llenó
    # production.env.example con lo que esto exige, y esto es la otra mitad).
    validate_storage_configuration()

    app_env = (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "development").strip().lower()
    is_production = app_env == "production"

    @app.before_request
    def cap_non_multipart_body():
        # Solo JSON/formularios sin archivo: un multipart de subida sigue sin
        # tope (decision del producto -- "app reservada", ver README), pero
        # nada obliga a que un POST de texto normal (crear un album, cambiar
        # una contraseña) pueda mandar un cuerpo de gigabytes solo para que el
        # servidor lo bufee en memoria antes de rechazarlo por longitud de
        # campo. `request.max_content_length` es por-peticion (Werkzeug
        # 2.3+): no toca el `MAX_CONTENT_LENGTH` global, que sigue sin fijar.
        if not (request.content_type or "").startswith("multipart/form-data"):
            request.max_content_length = int_env("MAX_JSON_BODY_KB", 256) * 1024

    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # `geolocation=(self)` y no `()`: la app SÍ pide la ubicación —"Estoy
        # aquí" en el selector de mapa y el clima del inicio— así que negarla
        # aquí describe mal lo que hace. Hoy no rompe nada porque en producción
        # Nginx sirve el index.html y esta cabecera solo viaja en respuestas de
        # la API, donde `Permissions-Policy` es inerte; pero el día que alguien
        # sirva el frontend desde Flask, o copie esta línea al Nginx, la
        # geolocalización dejaría de funcionar sin ningún error visible.
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(self)"
        response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        response.headers["Cross-Origin-Resource-Policy"] = "same-site"
        if request.path.startswith(("/api/", "/auth/")):
            response.headers["Cache-Control"] = "no-store, private, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            # La credencial viaja en cookie desde S01, no en `Authorization`:
            # una cache compartida tiene que separar por cookie o serviria la
            # respuesta de una sesion a otra.
            response.headers.add("Vary", "Cookie")
        if is_production and _truthy("HTTPS_ENABLED"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    register_error_handlers(app)
    setup_logging(app)
    register_blueprints(app)
    return app


app = create_app()
