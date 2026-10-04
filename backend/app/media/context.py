"""Contexto de un recuerdo: lugar, sol, festivo y clima.

Vive fuera de `app/api/` porque no es una preocupación de la API: el mismo
enriquecimiento corre al **subir** una foto, al **editarla** y desde los
endpoints que lo piden a mano. Aquí no hay rutas ni `request`; todo recibe la
conexión y devuelve datos, que es lo que permite reutilizarlo desde los tres
sitios sin pasar por HTTP.

Contrato de toda la familia `_try_auto_*`: **nunca lanzan**. Corren dentro de
una subida ya escrita en disco, así que una excepción aquí tiraría una subida
perfectamente válida. Sin clave, sin cupo o con el proveedor caído, la foto se
guarda igual y se queda sin esa capa de contexto.
"""
import json
import logging
import os

from ..integrations.locationiq import LocationIQError, reverse_geocode
from ..integrations.nager_date import HolidayError, find_holiday, get_holidays
from ..integrations.sunrise_sunset import SolarError, get_solar_times
from ..integrations.usage_budget import try_reserve
from ..integrations.visual_crossing import WeatherError, get_historical_weather
from ..utils.env import int_env
from ..utils.sql_security import execute_safe

LOCATION_PROVIDER = "local-coordinates"
SOLAR_PROVIDER = "local-calculation"
HOLIDAY_PROVIDER = "nager.date"
WEATHER_PROVIDER = "visualcrossing"


def _read_context(conn, media_id: int):
    return execute_safe(
        conn,
        """
        SELECT place_display_name, locality, region, country_code, country_name,
               timezone, location_provider, location_enriched_at,
               latitude, longitude,
               sunrise_at, sunset_at, solar_provider, solar_enriched_at,
               holiday_name, holiday_type, holiday_provider, holiday_enriched_at,
               weather_temp_c, weather_condition, weather_icon,
               weather_precip_mm, weather_wind_kph, weather_provider, weather_enriched_at
        FROM media_context WHERE media_id = :media_id
        """,
        {"media_id": media_id},
    ).mappings().first()

def _shape_context(context) -> dict | None:
    """`media_context` tal cual sale de la base -> JSON serializable. Las
    columnas NUMERIC vuelven de psycopg2 como Decimal, que `jsonify` no sabe
    convertir (el mismo detalle que ya obligó a `_shape_exif`)."""
    if not context:
        return None
    datos = dict(context)
    for campo in ("latitude", "longitude", "weather_temp_c", "weather_precip_mm", "weather_wind_kph"):
        if datos.get(campo) is not None:
            datos[campo] = float(datos[campo])
    return datos

def _write_context(conn, media_id: int, place: dict, provider: str, latitude=None, longitude=None) -> None:
    """El único INSERT/UPDATE de `media_context`, compartido por las tres vías
    que pueden llenarlo: LocationIQ explícito, LocationIQ automático al subir,
    y el lugar que escribe el dueño a mano.

    Guarda también la coordenada que produjo este contexto. Es la única copia
    para una foto ubicada con el pin manual (que no deja fila en `media_exif`),
    y sin ella no habría con qué consultar el sol ni refrescar el lugar."""
    execute_safe(
        conn,
        """
        INSERT INTO media_context (
            media_id, place_display_name, locality, region, country_code,
            country_name, timezone, location_provider, location_enriched_at,
            latitude, longitude
        )
        VALUES (
            :media_id, :display_name, :locality, :region, :country_code,
            :country_name, :timezone, :provider, NOW(),
            :latitude, :longitude
        )
        ON CONFLICT (media_id) DO UPDATE SET
            place_display_name = EXCLUDED.place_display_name,
            locality = EXCLUDED.locality,
            region = EXCLUDED.region,
            country_code = EXCLUDED.country_code,
            country_name = EXCLUDED.country_name,
            timezone = EXCLUDED.timezone,
            location_provider = EXCLUDED.location_provider,
            location_enriched_at = NOW(),
            latitude = EXCLUDED.latitude,
            longitude = EXCLUDED.longitude,
            updated_at = NOW()
        """,
        {
            "media_id": media_id,
            "display_name": place.get("display_name"),
            "locality": place.get("locality"),
            "region": place.get("region"),
            "country_code": place.get("country_code"),
            "country_name": place.get("country_name"),
            "timezone": place.get("timezone"),
            "provider": provider,
            "latitude": latitude,
            "longitude": longitude,
        },
    )

def _context_coordinates(conn, media_id: int):
    """Las coordenadas con las que se puede volver a consultar algo de esta
    foto: las que guardó el contexto (valen para EXIF y para pin manual) y,
    si aún no hay contexto, las del EXIF."""
    fila = execute_safe(
        conn,
        """
        SELECT COALESCE(mc.latitude, me.latitude) AS latitude,
               COALESCE(mc.longitude, me.longitude) AS longitude
        FROM assets m
        LEFT JOIN media_context mc ON mc.media_id = m.id
        LEFT JOIN media_exif me ON me.media_id = m.id
        WHERE m.id = :media_id
        """,
        {"media_id": media_id},
    ).mappings().first()
    if not fila or fila["latitude"] is None or fila["longitude"] is None:
        return None
    return float(fila["latitude"]), float(fila["longitude"])

def _write_solar(conn, media_id: int, times: dict, provider: str) -> None:
    """Unico UPDATE de las columnas solares. Siempre corre despues de que
    exista la fila (la crea `_write_context`), por eso es UPDATE y no UPSERT:
    sin lugar no hay contexto donde colgar la hora solar."""
    execute_safe(
        conn,
        """
        UPDATE media_context
        SET sunrise_at = :sunrise_at,
            sunset_at = :sunset_at,
            solar_provider = :provider,
            solar_enriched_at = NOW(),
            updated_at = NOW()
        WHERE media_id = :media_id
        """,
        {
            "media_id": media_id,
            "sunrise_at": times.get("sunrise_at"),
            "sunset_at": times.get("sunset_at"),
            "provider": provider,
        },
    )

def _cached_holidays(conn, country_code: str, year: int) -> list[dict]:
    """Los festivos de ese país y año, de la cache o del proveedor. Una vez
    cacheado un año, ningún recuerdo posterior de ese año vuelve a la red."""
    fila = execute_safe(
        conn,
        "SELECT payload FROM holiday_cache WHERE country_code = :country_code AND year = :year",
        {"country_code": country_code, "year": year},
    ).mappings().first()
    if fila:
        payload = fila["payload"]
        # psycopg2 devuelve JSONB ya deserializado; si alguna vez llegara
        # como texto, esto lo cubre sin romperse.
        return payload if isinstance(payload, list) else json.loads(payload)

    festivos = get_holidays(country_code, year)
    execute_safe(
        conn,
        """
        INSERT INTO holiday_cache (country_code, year, payload)
        VALUES (:country_code, :year, CAST(:payload AS JSONB))
        ON CONFLICT (country_code, year) DO UPDATE
            SET payload = EXCLUDED.payload, fetched_at = NOW()
        """,
        {"country_code": country_code, "year": year, "payload": json.dumps(festivos)},
    )
    return festivos

def _write_holiday(conn, media_id: int, holiday: dict | None, provider: str) -> None:
    """Único UPDATE de las columnas de festivo. `holiday` en None es un
    resultado válido —ese día no fue festivo— y se marca igual con
    `holiday_enriched_at`, para no volver a preguntarlo eternamente."""
    execute_safe(
        conn,
        """
        UPDATE media_context
        SET holiday_name = :name,
            holiday_type = :type,
            holiday_provider = :provider,
            holiday_enriched_at = NOW(),
            updated_at = NOW()
        WHERE media_id = :media_id
        """,
        {
            "media_id": media_id,
            "name": holiday.get("name") if holiday else None,
            "type": (holiday.get("types") or [None])[0] if holiday else None,
            "provider": provider,
        },
    )

def _try_auto_holiday(conn, media_id: int, country_code, taken_at) -> None:
    """Festivo del día del recuerdo, en la misma subida. Nunca lanza: sin
    país, sin fecha o con Nager.Date caído la foto se sube igual."""
    if not country_code or taken_at in (None, ""):
        return
    fecha = str(taken_at)[:10]
    try:
        festivos = _cached_holidays(conn, str(country_code).upper(), int(fecha[:4]))
    except HolidayError as exc:
        logging.warning("Auto-revelado de festivo al subir fallo (%s)", exc.reason)
        return
    except (TypeError, ValueError):
        return
    _write_holiday(conn, media_id, find_holiday(festivos, fecha), HOLIDAY_PROVIDER)

def _write_weather(conn, media_id: int, weather: dict, provider: str) -> None:
    """Único UPDATE de las columnas de clima."""
    execute_safe(
        conn,
        """
        UPDATE media_context
        SET weather_temp_c = :temp,
            weather_condition = :condition,
            weather_icon = :icon,
            weather_precip_mm = :precip,
            weather_wind_kph = :wind,
            weather_provider = :provider,
            weather_enriched_at = NOW(),
            updated_at = NOW()
        WHERE media_id = :media_id
        """,
        {
            "media_id": media_id,
            "temp": weather.get("temperature_c"),
            "condition": weather.get("condition"),
            "icon": weather.get("icon"),
            "precip": weather.get("precip_mm"),
            "wind": weather.get("wind_kph"),
            "provider": provider,
        },
    )

def _fetch_weather(conn, media_id: int, latitude, longitude, taken_at) -> str:
    """Clima histórico del día. Devuelve el estado para el informe del
    endpoint unificado: no es un error que falte la clave o la cuota, es que
    ese trozo del contexto no se puede completar hoy."""
    if taken_at in (None, ""):
        return "missing_date"
    api_key = (os.getenv("VISUAL_CROSSING_API_KEY") or "").strip()
    if not api_key:
        return "not_configured"
    daily_budget = int_env("VISUAL_CROSSING_DAILY_BUDGET", 900)
    if not try_reserve(conn, WEATHER_PROVIDER, daily_budget):
        return "quota_exhausted"
    try:
        weather = get_historical_weather(float(latitude), float(longitude), str(taken_at)[:10], api_key=api_key)
    except WeatherError as exc:
        logging.warning("Clima historico fallo (%s)", exc.reason)
        return exc.reason
    except (TypeError, ValueError):
        return "unavailable"
    _write_weather(conn, media_id, weather, WEATHER_PROVIDER)
    return "updated"

def _clear_date_dependent_context(conn, media_id: int) -> None:
    """Sol, festivo y clima dependen del DÍA de la foto. Cuando la fecha
    queda vacía (se borró y la foto tampoco trae una en su EXIF) no hay día
    al que atarlos, así que se limpian en vez de dejar colgados los de la
    fecha anterior."""
    execute_safe(
        conn,
        """
        UPDATE media_context
        SET sunrise_at = NULL, sunset_at = NULL, solar_provider = NULL, solar_enriched_at = NULL,
            holiday_name = NULL, holiday_type = NULL, holiday_provider = NULL, holiday_enriched_at = NULL,
            weather_temp_c = NULL, weather_condition = NULL, weather_icon = NULL,
            weather_precip_mm = NULL, weather_wind_kph = NULL, weather_provider = NULL, weather_enriched_at = NULL,
            updated_at = NOW()
        WHERE media_id = :media_id
        """,
        {"media_id": media_id},
    )

def _try_auto_solar(conn, media_id: int, latitude, longitude, taken_at) -> None:
    """Amanecer/atardecer del día de la foto, en la misma subida. Nunca lanza,
    igual que el EXIF y el lugar: sin fecha, sin coordenadas o con el
    proveedor caído la foto se sube igual y queda el botón manual."""
    if taken_at in (None, ""):
        return
    fecha = str(taken_at)[:10]
    try:
        times = get_solar_times(float(latitude), float(longitude), fecha)
    except SolarError as exc:
        logging.warning("Auto-revelado solar al subir fallo (%s)", exc.reason)
        return
    except (TypeError, ValueError):
        return
    if times.get("sunrise_at") or times.get("sunset_at"):
        _write_solar(conn, media_id, times, SOLAR_PROVIDER)

def _try_auto_enrich(conn, media_id: int, latitude, longitude, taken_at=None) -> None:
    """Save GPS coordinates and locally calculated solar times."""
    try:
        latitude = float(latitude)
        longitude = float(longitude)
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return
    except (TypeError, ValueError):
        return
    place = {
        "display_name": f"{latitude:.5f}, {longitude:.5f}",
        "locality": None,
        "region": None,
        "country_code": None,
        "country_name": None,
        "timezone": None,
    }
    _write_context(conn, media_id, place, LOCATION_PROVIDER, latitude, longitude)
    _try_auto_solar(conn, media_id, latitude, longitude, taken_at)
