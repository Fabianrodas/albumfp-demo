"""Rutas del contexto de un recuerdo y de las dos funciones que mandan la
imagen fuera (OCR y sugerencias de etiquetas).

Cuelgan del mismo `media_bp` que el resto de media —para el cliente son las
mismas URLs de siempre— pero viven aparte porque `media.py` era con diferencia
el fichero más grande del backend, y esto es un tema propio: leer y completar
las capas de contexto, no el CRUD de fotos.

La lógica de enriquecimiento no está aquí sino en `app/media/context.py`, que
es la que comparten la subida, la edición y estos endpoints.
"""
import logging
import os

from flask import request
from ..security.sessions import current_user_id, session_required

from ..db.db import db_conn
from ..integrations.imagga import (CONFIANZA_MINIMA, CONFIANZA_MINIMA_CONOCIDA, MAX_SUGERENCIAS,
                                   ImaggaError, suggest_tags)
from ..integrations.locationiq import LocationIQError, reverse_geocode
from ..integrations.nager_date import HolidayError, find_holiday
from ..integrations.ocr_space import OcrError, extract_text
from ..integrations.sunrise_sunset import SolarError, get_solar_times
from ..integrations.usage_budget import try_reserve
from ..media.context import (HOLIDAY_PROVIDER, LOCATION_PROVIDER, SOLAR_PROVIDER,
                             _cached_holidays, _context_coordinates, _fetch_weather,
                             _read_context, _shape_context, _write_context,
                             _write_holiday, _write_solar)
from ..media.external_copy import temporary_jpeg_for_external_service
from ..media.assets import require_asset_capability, require_asset_permission
from ..storage.backends import get_storage_backend
from ..storage.contracts import ObjectNotFound, StorageError
from ..storage.object_keys import InvalidStorageKey, validate_storage_key
from ..storage.workspace import local_workspace
from ..utils.env import int_env
from ..utils.responses import fail, ok
from ..utils.sql_security import execute_safe
from .media import media_bp

OCR_PROVIDER = "ocr.space"
TAGS_PROVIDER = "imagga"


@media_bp.get("/media/<int:media_id>/context")
@session_required
def get_media_context(media_id: int):
    """Lectura del lugar ya resuelto, si lo hay. No llama a ningun proveedor:
    solo devuelve lo que `enrich_media_location` haya guardado antes."""
    user_id = current_user_id()
    with db_conn() as conn:
        asset, access = require_asset_permission(conn, media_id, user_id, "read")
        if not asset or not access:
            return fail("Media no encontrada", status=404)
        context = _read_context(conn, media_id)
    # `ok(data=None, ...)` omite la clave "data" entera (asi la usan el resto
    # de endpoints que no devuelven nada); anidar bajo "context" deja
    # representar honestamente "todavia no hay contexto" con un null real, en
    # vez de que la ausencia de contexto se confunda con la ausencia de dato.
    return ok(data={"context": _shape_context(context)}, message="Contexto del recuerdo")

@media_bp.post("/media/<int:media_id>/context/location")
@session_required
def enrich_media_location(media_id: int):
    """Convierte el GPS del EXIF en un lugar legible via LocationIQ.

    Solo el dueño puede disparar la llamada externa (lectura ya la cubre
    `get_media_context`). Con contexto en cache y sin `refresh` explicito no
    se llama al proveedor ni se gasta presupuesto: esta es la unica rama que
    puede tocar la red, y solo se llega a ella cuando de verdad hace falta.
    """
    user_id = current_user_id()
    payload = request.get_json(silent=True) or {}
    refresh = bool(payload.get("refresh"))

    with db_conn() as conn:
        asset, readable = require_asset_permission(conn, media_id, user_id, "read")
        if not asset or not readable:
            return fail("Media no encontrada", status=404)
        asset, access = require_asset_capability(conn, media_id, user_id, "edit_media")
        # Mismo permiso que editar la foto: rellenar el lugar desde su GPS es
        # exactamente lo que hace el editor cuando le cambias la ubicacion.
        if not access:
            return fail("No autorizado para completar el contexto", status=403)

        coordenadas = _context_coordinates(conn, media_id)
        if not coordenadas:
            return fail("Esta foto no tiene coordenadas GPS", status=400)

        existing = _read_context(conn, media_id)
        if existing and existing["location_enriched_at"] and not refresh:
            return ok(data=_shape_context(existing), message="Contexto del recuerdo")

        api_key = (os.getenv("LOCATIONIQ_API_KEY") or "").strip()
        if not api_key:
            return fail(
                "Esta función aún no está configurada en este servidor.",
                status=503,
                code="integration_not_configured",
            )

        daily_budget = int_env("LOCATIONIQ_DAILY_BUDGET", 4500)
        if not try_reserve(conn, LOCATION_PROVIDER, daily_budget):
            return fail(
                "La cuota gratuita de esta función se alcanzó por hoy. Intenta de nuevo mañana.",
                status=429,
                code="integration_quota_exhausted",
            )

        try:
            place = reverse_geocode(coordenadas[0], coordenadas[1], api_key=api_key)
        except LocationIQError as exc:
            status = 429 if exc.reason == "quota" else 503
            return fail(
                "No pudimos consultar el servicio ahora. Tu archivo no se modificó.",
                status=status,
                code=f"locationiq_{exc.reason}",
            )

        _write_context(conn, media_id, place, LOCATION_PROVIDER, coordenadas[0], coordenadas[1])
        context = _read_context(conn, media_id)

    return ok(data=_shape_context(context), message="Contexto completado")

@media_bp.post("/media/<int:media_id>/context/solar")
@session_required
def enrich_media_solar(media_id: int):
    """Amanecer y atardecer del día en que se tomó la foto.

    Mismo permiso y misma forma que el lugar (`edit_media`): sigue existiendo
    para fotos subidas antes de esta fase o cuyo revelado automático falló.
    Necesita las dos cosas —coordenadas y fecha— porque el sol depende de
    dónde Y de cuándo; sin cualquiera de ellas no hay nada que consultar.
    """
    user_id = current_user_id()
    payload = request.get_json(silent=True) or {}
    refresh = bool(payload.get("refresh"))

    with db_conn() as conn:
        asset, readable = require_asset_permission(conn, media_id, user_id, "read")
        if not asset or not readable:
            return fail("Media no encontrada", status=404)
        asset, access = require_asset_capability(conn, media_id, user_id, "edit_media")
        if not access:
            return fail("No autorizado para completar el contexto", status=403)
        media = execute_safe(
            conn,
            "SELECT id, taken_at FROM assets WHERE id = :media_id",
            {"media_id": media_id},
        ).mappings().first()
        if not media["taken_at"]:
            return fail("Esta foto no tiene fecha de captura", status=400)

        coordenadas = _context_coordinates(conn, media_id)
        if not coordenadas:
            return fail("Esta foto no tiene coordenadas GPS", status=400)

        existing = _read_context(conn, media_id)
        if not existing:
            return fail("Completa primero el lugar de esta foto", status=400)
        if existing["solar_enriched_at"] and not refresh:
            return ok(data=_shape_context(existing), message="Contexto del recuerdo")

        try:
            times = get_solar_times(coordenadas[0], coordenadas[1], str(media["taken_at"])[:10])
        except SolarError as exc:
            return fail(
                "No pudimos consultar el servicio ahora. Tu archivo no se modificó.",
                status=400 if exc.reason == "rejected" else 503,
                code=f"solar_{exc.reason}",
            )

        if not times.get("sunrise_at") and not times.get("sunset_at"):
            return fail("Ese día no hubo amanecer ni atardecer en ese lugar", status=400)

        _write_solar(conn, media_id, times, SOLAR_PROVIDER)
        context = _read_context(conn, media_id)

    return ok(data=_shape_context(context), message="Contexto solar completado")

@media_bp.post("/media/<int:media_id>/context/holiday")
@session_required
def enrich_media_holiday(media_id: int):
    """Si el día del recuerdo fue festivo nacional en el país donde se tomó.

    Necesita el país (que resuelve LocationIQ) y la fecha de captura. "Ese día
    no fue festivo" es un resultado correcto y se guarda como tal: sin eso,
    cada foto de un día normal volvería a preguntar para siempre.
    """
    user_id = current_user_id()
    payload = request.get_json(silent=True) or {}
    refresh = bool(payload.get("refresh"))

    with db_conn() as conn:
        asset, readable = require_asset_permission(conn, media_id, user_id, "read")
        if not asset or not readable:
            return fail("Media no encontrada", status=404)
        asset, access = require_asset_capability(conn, media_id, user_id, "edit_media")
        if not access:
            return fail("No autorizado para completar el contexto", status=403)
        media = execute_safe(
            conn,
            "SELECT id, taken_at FROM assets WHERE id = :media_id",
            {"media_id": media_id},
        ).mappings().first()
        if not media["taken_at"]:
            return fail("Esta foto no tiene fecha de captura", status=400)

        existing = _read_context(conn, media_id)
        if not existing or not existing["country_code"]:
            return fail("Completa primero el lugar de esta foto", status=400)
        if existing["holiday_enriched_at"] and not refresh:
            return ok(data=_shape_context(existing), message="Contexto del recuerdo")

        fecha = str(media["taken_at"])[:10]
        try:
            festivos = _cached_holidays(conn, existing["country_code"].upper(), int(fecha[:4]))
        except HolidayError as exc:
            return fail(
                "Nager.Date no cubre ese país." if exc.reason == "unsupported"
                else "No pudimos consultar el servicio ahora. Tu archivo no se modificó.",
                status=400 if exc.reason == "unsupported" else 503,
                code=f"holiday_{exc.reason}",
            )

        _write_holiday(conn, media_id, find_holiday(festivos, fecha), HOLIDAY_PROVIDER)
        context = _read_context(conn, media_id)

    return ok(data=_shape_context(context), message="Contexto de festivos completado")

@media_bp.post("/media/<int:media_id>/context/enrich")
@session_required
def enrich_media_context(media_id: int):
    """Completa TODO el contexto que se pueda de una vez: lugar, sol, festivo
    y clima.

    Sustituye a pulsar un botón por proveedor. Cada paso informa su propio
    estado y **ninguno detiene a los siguientes**: que falte la clave del
    clima, o que Nager.Date no cubra un país, no puede impedir que se revele
    el amanecer. Los endpoints por proveedor siguen existiendo para pedir uno
    concreto con `refresh`.
    """
    user_id = current_user_id()
    payload = request.get_json(silent=True) or {}
    refresh = bool(payload.get("refresh"))
    resultados = {}

    with db_conn() as conn:
        asset, readable = require_asset_permission(conn, media_id, user_id, "read")
        if not asset or not readable:
            return fail("Media no encontrada", status=404)
        asset, access = require_asset_capability(conn, media_id, user_id, "edit_media")
        if not access:
            return fail("No autorizado para completar el contexto", status=403)
        media = execute_safe(
            conn,
            "SELECT id, taken_at FROM assets WHERE id = :media_id",
            {"media_id": media_id},
        ).mappings().first()

        taken_at = media["taken_at"]
        coordenadas = _context_coordinates(conn, media_id)
        contexto = _read_context(conn, media_id)

        # 1. Lugar. Todo lo demás cuelga de esta fila, así que va primero.
        if not coordenadas:
            resultados["location"] = "missing_gps"
        elif contexto and contexto["location_enriched_at"] and not refresh:
            resultados["location"] = "cached"
        else:
            api_key = (os.getenv("LOCATIONIQ_API_KEY") or "").strip()
            if not api_key:
                resultados["location"] = "not_configured"
            elif not try_reserve(conn, LOCATION_PROVIDER, int_env("LOCATIONIQ_DAILY_BUDGET", 4500)):
                resultados["location"] = "quota_exhausted"
            else:
                try:
                    place = reverse_geocode(coordenadas[0], coordenadas[1], api_key=api_key)
                    _write_context(conn, media_id, place, LOCATION_PROVIDER, coordenadas[0], coordenadas[1])
                    resultados["location"] = "updated"
                except LocationIQError as exc:
                    resultados["location"] = exc.reason
            contexto = _read_context(conn, media_id)

        # Sin fila de contexto no hay dónde escribir lo demás.
        if not contexto:
            return ok(data={"context": None, "results": resultados}, message="Contexto del recuerdo")

        # 2. Sol.
        if not coordenadas:
            resultados["solar"] = "missing_gps"
        elif not taken_at:
            resultados["solar"] = "missing_date"
        elif contexto["solar_enriched_at"] and not refresh:
            resultados["solar"] = "cached"
        else:
            try:
                times = get_solar_times(coordenadas[0], coordenadas[1], str(taken_at)[:10])
                if times.get("sunrise_at") or times.get("sunset_at"):
                    _write_solar(conn, media_id, times, SOLAR_PROVIDER)
                    resultados["solar"] = "updated"
                else:
                    resultados["solar"] = "no_data"
            except SolarError as exc:
                resultados["solar"] = exc.reason

        # 3. Festivo.
        if not contexto["country_code"]:
            resultados["holiday"] = "missing_country"
        elif not taken_at:
            resultados["holiday"] = "missing_date"
        elif contexto["holiday_enriched_at"] and not refresh:
            resultados["holiday"] = "cached"
        else:
            fecha = str(taken_at)[:10]
            try:
                festivos = _cached_holidays(conn, contexto["country_code"].upper(), int(fecha[:4]))
                _write_holiday(conn, media_id, find_holiday(festivos, fecha), HOLIDAY_PROVIDER)
                resultados["holiday"] = "updated"
            except HolidayError as exc:
                resultados["holiday"] = exc.reason
            except (TypeError, ValueError):
                resultados["holiday"] = "unavailable"

        # 4. Clima.
        if not coordenadas:
            resultados["weather"] = "missing_gps"
        elif contexto["weather_enriched_at"] and not refresh:
            resultados["weather"] = "cached"
        else:
            resultados["weather"] = _fetch_weather(conn, media_id, coordenadas[0], coordenadas[1], taken_at)

        context = _read_context(conn, media_id)

    return ok(data={"context": _shape_context(context), "results": resultados}, message="Contexto del recuerdo")

def _owned_image_for_external_call(conn, media_id: int, user_id: int, accion: str):
    """Comprobaciones comunes a las DOS funciones que sacan los píxeles de una
    foto del servidor (OCR y sugerencias de etiquetas).

    Devuelve `(storage_key, None)` si todo está en orden, o
    `(None, respuesta_de_error)` para que quien llame la devuelva tal cual.
    Nunca resuelve ni abre el objeto: eso lo hace quien llame, materializando
    la key justo antes de necesitar bytes de verdad (spec §6, §11).
    Ambas exigen **dueño real** —no una capacidad— porque decidir que la imagen
    viaje a un tercero no es lo mismo que editar un dato de la foto.
    """
    asset, readable = require_asset_permission(conn, media_id, user_id, "read")
    if not asset or not readable:
        return None, fail("Media no encontrada", status=404)
    asset, access = require_asset_permission(conn, media_id, user_id, "owner")
    if not access:
        return None, fail(f"Solo el dueño puede {accion}", status=403)

    fila = execute_safe(
        conn,
        "SELECT file_type, storage_path FROM assets WHERE id = :media_id",
        {"media_id": media_id},
    ).mappings().first()
    if fila["file_type"] != "image":
        return None, fail("Esta función solo funciona con fotos", status=400)

    try:
        clave = validate_storage_key(fila["storage_path"])
    except InvalidStorageKey:
        return None, fail("El archivo de esta media no está disponible", status=404)
    return clave, None

def _read_ocr(conn, media_id: int):
    return execute_safe(
        conn,
        """
        SELECT media_id, extracted_text, detected_language, provider, analyzed_at
        FROM media_ocr WHERE media_id = :media_id
        """,
        {"media_id": media_id},
    ).mappings().first()

@media_bp.get("/media/<int:media_id>/ocr")
@session_required
def get_media_ocr(media_id: int):
    """Texto ya detectado, si lo hay. No llama al proveedor: cualquiera que
    pueda ver la foto puede leer lo que el dueño haya extraido de ella."""
    user_id = current_user_id()
    with db_conn() as conn:
        asset, access = require_asset_permission(conn, media_id, user_id, "read")
        if not asset or not access:
            return fail("Media no encontrada", status=404)
        row = _read_ocr(conn, media_id)
    # Mismo patron que el contexto: anidar deja distinguir "todavia no se ha
    # analizado" de "no hay dato", que `data=None` no puede expresar.
    return ok(data={"ocr": dict(row) if row else None}, message="Texto detectado")

@media_bp.post("/media/<int:media_id>/ocr")
@session_required
def detect_media_ocr(media_id: int):
    """Manda una copia temporal reducida de la foto a OCR.Space y guarda el
    texto que devuelva.

    **Solo el dueño**, y a proposito mas estricto que el resto de acciones de
    escritura: es la unica funcion de la app que saca los pixeles de una foto
    del servidor. Un colaborador con `edit_media` puede corregir datos de la
    foto, pero decidir que la imagen viaje a un tercero es del dueño.

    Volver a llamar reemplaza el texto guardado y gasta cuota otra vez: no hay
    cache que respetar aqui, porque el usuario solo vuelve a pedirlo cuando el
    resultado anterior no le sirvio.
    """
    user_id = current_user_id()
    with db_conn() as conn:
        clave, error = _owned_image_for_external_call(conn, media_id, user_id, "analizar el texto de una foto")
        if error:
            return error

        api_key = (os.getenv("OCR_SPACE_API_KEY") or "").strip()
        if not api_key:
            return fail(
                "Esta función aún no está configurada en este servidor.",
                status=503,
                code="integration_not_configured",
            )

        try:
            # La copia temporal se borra sola al salir del `with`, tambien si
            # el proveedor falla dentro. `materialize` cede el original (real
            # en local, descargado a un temporal en remote); la copia reducida
            # sale a un workspace "external-copy" aparte.
            with get_storage_backend().materialize(clave) as origen, \
                 local_workspace("external-copy") as espacio:
                with temporary_jpeg_for_external_service(origen, espacio.directory) as copia:
                    # La cuota del proveedor se reserva AQUÍ: si el storage
                    # falla, no se gasta una llamada del free tier.
                    if not try_reserve(conn, OCR_PROVIDER, int_env("OCR_SPACE_DAILY_BUDGET", 450)):
                        return fail(
                            "La cuota gratuita de esta función se alcanzó por hoy. Intenta de nuevo mañana.",
                            status=429,
                            code="integration_quota_exhausted",
                        )
                    resultado = extract_text(copia, api_key=api_key)
        except ObjectNotFound:
            return fail("El archivo de esta media no está disponible", status=404)
        except StorageError:
            return fail("El almacenamiento no está disponible ahora", status=503,
                        code="media_storage_unavailable")
        except OcrError as exc:
            status = 429 if exc.reason == "quota" else 503
            return fail(
                "No pudimos analizar la imagen ahora. Tu archivo no se modificó.",
                status=status,
                code=f"ocr_{exc.reason}",
            )
        except OSError:
            logging.warning("No se pudo preparar la copia temporal para OCR")
            return fail("No pudimos preparar esta imagen. Tu archivo no se modificó.", status=503)

        texto = (resultado.get("text") or "").strip()
        if not texto:
            # Una foto sin texto es un resultado valido, no un fallo. No se
            # guarda fila: `extracted_text` es NOT NULL y una cadena vacia no
            # dice nada que la ausencia de fila no diga ya.
            execute_safe(conn, "DELETE FROM media_ocr WHERE media_id = :media_id", {"media_id": media_id})
            return ok(data={"ocr": None}, message="No se encontró texto en esta foto")

        execute_safe(
            conn,
            """
            INSERT INTO media_ocr (media_id, extracted_text, detected_language, provider, analyzed_at)
            VALUES (:media_id, :texto, :idioma, :provider, NOW())
            ON CONFLICT (media_id) DO UPDATE
            SET extracted_text = EXCLUDED.extracted_text,
                detected_language = EXCLUDED.detected_language,
                provider = EXCLUDED.provider,
                analyzed_at = NOW()
            """,
            {"media_id": media_id, "texto": texto, "idioma": resultado.get("language"), "provider": OCR_PROVIDER},
        )
        row = _read_ocr(conn, media_id)

    return ok(data={"ocr": dict(row)}, message="Texto detectado")

def _rank_suggestions(conn, owner_id: int, crudas: list[dict]) -> list[dict]:
    """Antepone las etiquetas que YA existen en la biblioteca.

    Reutilizar una etiqueta que ya existe siempre es mejor que inventar una
    parecida: mantiene el catálogo pequeño y hace que la búsqueda por tag
    encuentre de verdad todas las fotos del tema. Por eso una etiqueta
    conocida se propone con **menos** confianza (30%) que una nueva (55%), y
    sale primero en la lista.

    `tag_id` no nulo es la señal de "esta ya existe": el frontend la usa para
    agruparlas y para asignarla sin crear nada.
    """
    if not crudas:
        return []

    filas = execute_safe(
        conn,
        "SELECT id, name FROM tags WHERE owner_id = :owner_id AND LOWER(name) = ANY(:nombres)",
        {"owner_id": owner_id, "nombres": [s["name"] for s in crudas]},
    ).mappings().all()
    conocidas = {fila["name"].strip().lower(): fila["id"] for fila in filas}

    elegidas = []
    for sugerencia in crudas:
        tag_id = conocidas.get(sugerencia["name"])
        if tag_id is None and sugerencia["confidence"] < CONFIANZA_MINIMA:
            continue
        elegidas.append({**sugerencia, "tag_id": tag_id})

    # Conocidas primero; dentro de cada grupo, la más segura arriba.
    elegidas.sort(key=lambda s: (s["tag_id"] is None, -s["confidence"]))
    return elegidas[:MAX_SUGERENCIAS]

@media_bp.post("/media/<int:media_id>/tag-suggestions")
@session_required
def suggest_media_tags(media_id: int):
    """Sugiere etiquetas para una foto con Imagga. **No crea ni asigna nada.**

    La respuesta es efimera y no se guarda en ninguna tabla: el usuario marca
    las que quiera y las confirma con los endpoints de tags de siempre. Así,
    una sugerencia mala no deja rastro y el flujo manual sigue siendo el mismo.

    Solo el dueño, igual que el OCR y por la misma razón: es la otra función
    que saca los píxeles de la foto del servidor.
    """
    user_id = current_user_id()
    with db_conn() as conn:
        clave, error = _owned_image_for_external_call(conn, media_id, user_id, "pedir sugerencias de etiquetas")
        if error:
            return error

        api_key = (os.getenv("IMAGGA_API_KEY") or "").strip()
        api_secret = (os.getenv("IMAGGA_API_SECRET") or "").strip()
        if not api_key or not api_secret:
            return fail(
                "Esta función aún no está configurada en este servidor.",
                status=503,
                code="integration_not_configured",
            )

        try:
            with get_storage_backend().materialize(clave) as origen, \
                 local_workspace("external-copy") as espacio:
                with temporary_jpeg_for_external_service(origen, espacio.directory) as copia:
                    # Mensual, no diario: el cupo gratuito de Imagga son 100 al
                    # mes. Se reserva AQUÍ, después de preparar la copia: si el
                    # storage falla, no se gasta una llamada del free tier.
                    if not try_reserve(conn, TAGS_PROVIDER, int_env("IMAGGA_MONTHLY_BUDGET", 90), period="month"):
                        return fail(
                            "La cuota gratuita de esta función se alcanzó este mes. Las etiquetas manuales siguen funcionando.",
                            status=429,
                            code="integration_quota_exhausted",
                        )
                    # Se piden MÁS de las que se van a enseñar y con el suelo
                    # bajo: el filtro fino se hace aquí abajo, donde sí se sabe
                    # qué etiquetas ya existen en la biblioteca.
                    crudas = suggest_tags(
                        copia,
                        api_key=api_key,
                        api_secret=api_secret,
                        min_confidence=CONFIANZA_MINIMA_CONOCIDA,
                        limit=MAX_SUGERENCIAS * 4,
                    )
        except ObjectNotFound:
            return fail("El archivo de esta media no está disponible", status=404)
        except StorageError:
            return fail("El almacenamiento no está disponible ahora", status=503,
                        code="media_storage_unavailable")
        except ImaggaError as exc:
            status = 429 if exc.reason == "quota" else 503
            return fail(
                "No pudimos sugerir etiquetas ahora. Tu archivo no se modificó.",
                status=status,
                code=f"imagga_{exc.reason}",
            )
        except OSError:
            logging.warning("No se pudo preparar la copia temporal para Imagga")
            return fail("No pudimos preparar esta imagen. Tu archivo no se modificó.", status=503)

        # `_owned_image_for_external_call` ya exigió dueño real del álbum, así
        # que quien pide las sugerencias es el propietario del vocabulario.
        sugerencias = _rank_suggestions(conn, user_id, crudas)

    return ok(data={"suggestions": sugerencias}, message="Etiquetas sugeridas")

@media_bp.delete("/media/<int:media_id>/ocr")
@session_required
def delete_media_ocr(media_id: int):
    """Borra el texto detectado. Solo el dueño, igual que analizarlo."""
    user_id = current_user_id()
    with db_conn() as conn:
        asset, readable = require_asset_permission(conn, media_id, user_id, "read")
        if not asset or not readable:
            return fail("Media no encontrada", status=404)
        asset, access = require_asset_permission(conn, media_id, user_id, "owner")
        if not access:
            return fail("Solo el dueño puede borrar el texto detectado", status=403)
        execute_safe(conn, "DELETE FROM media_ocr WHERE media_id = :media_id", {"media_id": media_id})
    return ok(message="Texto detectado eliminado")
