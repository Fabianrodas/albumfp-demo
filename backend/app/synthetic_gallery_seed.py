"""One-time, local-only synthetic gallery seed for AlbumFP Demo screenshots."""

from __future__ import annotations

import io
import ipaddress
import os

from PIL import Image, ImageDraw


SYNTHETIC_USERNAME = "albumfp_demo_synthetic_gallery"
SYNTHETIC_FULL_NAME = "Cuenta de muestra local"
ALBUM_TITLE = "Recuerdos de muestra"

_SCENES = (
    ((37, 67, 106), (244, 172, 119), (255, 221, 137), (49, 85, 105), (36, 57, 76)),
    ((57, 126, 156), (195, 224, 203), (255, 231, 165), (72, 129, 132), (34, 89, 99)),
    ((49, 91, 129), (248, 176, 131), (255, 221, 159), (80, 121, 131), (35, 68, 84)),
    ((61, 111, 104), (192, 216, 172), (255, 225, 160), (88, 134, 94), (47, 86, 67)),
)

_MEDIA = (
    ("Amanecer en la cordillera", "Colores de mañana en un paisaje ilustrado."),
    ("Luz de tarde junto al lago", "Una escena sintética junto al agua."),
    ("Costa tranquila al final del día", "Cielo y costa creados para esta Demo."),
    ("Jardín después de la lluvia", "Verdes suaves en una ilustración original."),
)


def validate_server_identity(database: str, server_address: str | None, port: str) -> None:
    """Refuse every target except the dedicated IPv4/IPv6 loopback Demo DB."""
    if database != "albumfp_demo" or str(port) != "55432":
        raise ValueError("El seed solo puede usar albumfp_demo en el puerto 55432")
    try:
        address = ipaddress.ip_interface(server_address or "").ip
    except ValueError as exc:
        raise ValueError("El servidor PostgreSQL debe aceptar conexiones loopback") from exc
    if not address.is_loopback:
        raise ValueError("El servidor PostgreSQL debe aceptar conexiones loopback")


def require_open_registration_for_seed(mode: str | None) -> None:
    """Do not let a local seed bypass closed or invite-only registration."""
    if mode != "open":
        raise RuntimeError("El seed requiere REGISTRATION_MODE=open; no modifica el modo configurado")


def validate_configured_target(database: str | None, host: str | None, port: int | None) -> None:
    """Validate SQLAlchemy's parsed endpoint before opening a connection."""
    if (
        database != "albumfp_demo"
        or str(port) != "55432"
        or (host or "").lower() not in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError("El seed solo puede usar albumfp_demo en PostgreSQL loopback:55432")


def synthetic_scene_jpeg(scene: int) -> bytes:
    """Generate an original, EXIF-free landscape illustration in memory."""
    if isinstance(scene, bool) or not isinstance(scene, int) or not 0 <= scene < len(_SCENES):
        raise ValueError("Escena sintética desconocida")

    sky_top, sky_bottom, sun_color, far_hill, near_hill = _SCENES[scene]
    width, height = 1200, 900
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        fraction = y / (height - 1)
        color = tuple(
            round(start * (1 - fraction) + end * fraction)
            for start, end in zip(sky_top, sky_bottom)
        )
        for x in range(width):
            pixels[x, y] = color

    draw = ImageDraw.Draw(image)
    sun_x = (820, 840, 920, 310)[scene]
    sun_y = (260, 280, 300, 235)[scene]
    draw.ellipse((sun_x - 105, sun_y - 105, sun_x + 105, sun_y + 105), fill=sun_color)

    ridge_sets = (
        ((0, 635), (220, 355), (385, 515), (625, 290), (875, 535), (1055, 380), (1200, 565)),
        ((0, 555), (245, 385), (425, 505), (690, 350), (915, 490), (1090, 370), (1200, 530)),
        ((0, 575), (210, 430), (455, 510), (690, 350), (890, 465), (1095, 345), (1200, 500)),
        ((0, 550), (210, 410), (420, 500), (670, 345), (895, 470), (1080, 360), (1200, 505)),
    )
    draw.polygon([(0, height), *ridge_sets[scene], (width, height)], fill=far_hill)
    draw.polygon(
        [(0, 725), (200, 570), (365, 695), (610, 520), (805, 680),
         (1000, 535), (1200, 685), (1200, height), (0, height)],
        fill=near_hill,
    )

    # Simple, clearly illustrated foreground details; nothing is copied from a photo.
    for tree_x, tree_y, scale in ((150, 690, 1.0), (1040, 660, 1.25), (930, 730, 0.72)):
        trunk = (tree_x - 8 * scale, tree_y, tree_x + 8 * scale, tree_y + 115 * scale)
        draw.rectangle(trunk, fill=(60, 54, 47))
        for offset, radius in ((0, 43), (-31, 32), (30, 29)):
            center_y = tree_y - 22 * scale + offset * scale
            radius *= scale
            draw.ellipse(
                (tree_x - radius, center_y - radius, tree_x + radius, center_y + radius),
                fill=far_hill,
            )

    output = io.BytesIO()
    image.save(output, format="JPEG", quality=90, optimize=True, exif=b"")
    return output.getvalue()


def run_seed(password: str) -> tuple[int, int]:
    """Create a new synthetic-only account, album, and four local images."""
    app_environment = (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "development").strip().lower()
    test_mode = (os.getenv("ALBUMFP_DEMO_TEST_MODE") or "").strip().lower()
    if app_environment not in {"development", "dev", "local"} or test_mode in {"1", "true", "yes", "on"}:
        raise RuntimeError("El seed requiere el entorno local de desarrollo, nunca test ni producción")

    from app.db.db import db_conn, engine
    from app.db.safety import database_url_from_environment
    from app.domain.rules import registration_mode
    from app.security.password_policy import validate_password
    from app.storage.backends import validate_storage_configuration
    from app.utils.sql_security import execute_safe

    # Validate both the configured URL and the live PostgreSQL endpoint before
    # the first write. The URL helper allowlists only the development DB.
    database_url_from_environment(purpose="development")
    validate_configured_target(engine.url.database, engine.url.host, engine.url.port)
    validate_storage_configuration()
    valid, error = validate_password(password)
    if not valid:
        raise ValueError(error or "La contraseña no cumple la política local")
    require_open_registration_for_seed(registration_mode())

    # Importing the app only validates its local configuration; it opens no
    # listener and makes no network calls. Do it before creating the account.
    from application import app as local_app
    from app.security.sessions import csrf_cookie_name

    with db_conn() as conn:
        identity = conn.exec_driver_sql(
            "SELECT current_database(), inet_server_addr()::text, current_setting('port')"
        ).one()
        validate_server_identity(*identity)
        existing = execute_safe(
            conn,
            "SELECT id FROM users WHERE username = :username LIMIT 1",
            {"username": SYNTHETIC_USERNAME},
        ).first()
        if existing:
            raise RuntimeError(
                "La cuenta sintética ya existe; el seed no modifica cuentas existentes"
            )
    # Exercise the same authenticated, CSRF-protected routes and upload/storage
    # pipeline as a user of the local app. Flask's test client opens no socket.
    titles: list[str] = []
    with local_app.test_client() as client:
        registration = client.post(
            "/auth/register",
            json={"username": SYNTHETIC_USERNAME, "full_name": SYNTHETIC_FULL_NAME, "password": password},
        )
        if registration.status_code != 201:
            raise RuntimeError("No se pudo registrar la cuenta sintética local")
        login = client.post(
            "/auth/login",
            json={"username": SYNTHETIC_USERNAME, "password": password},
        )
        if login.status_code != 200:
            raise RuntimeError("No se pudo iniciar la sesión sintética local")
        csrf_cookie = client.get_cookie(csrf_cookie_name())
        if not csrf_cookie:
            raise RuntimeError("La sesión local no entregó su cookie CSRF")
        headers = {"X-CSRF-Token": csrf_cookie.value}

        album_response = client.post(
            "/api/albums",
            json={
                "titulo": ALBUM_TITLE,
                "descripcion": "Ilustraciones creadas localmente para las capturas de AlbumFP Demo.",
                "is_private": True,
            },
            headers=headers,
        )
        if album_response.status_code != 201:
            raise RuntimeError("No se pudo crear el álbum sintético local")
        album_id = int(album_response.get_json()["data"]["id"])

        media_ids: list[int] = []
        for index, (title, caption) in enumerate(_MEDIA):
            response = client.post(
                f"/api/albums/{album_id}/media",
                data={
                    "title": title,
                    "caption": caption,
                    "file": (io.BytesIO(synthetic_scene_jpeg(index)), f"muestra-{index + 1}.jpg", "image/jpeg"),
                },
                headers=headers,
                content_type="multipart/form-data",
            )
            if response.status_code != 201:
                raise RuntimeError(f"No se pudo guardar la imagen sintética {index + 1}")
            data = response.get_json().get("data") or {}
            media_ids.append(int(data["id"]))
            titles.append(title)

        cover = client.patch(
            f"/api/albums/{album_id}",
            json={"cover_media_id": media_ids[0]},
            headers=headers,
        )
        if cover.status_code != 200:
            raise RuntimeError("No se pudo asignar la portada sintética")

    return album_id, len(titles)
