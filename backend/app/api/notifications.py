from flask import Blueprint, request
from ..security.sessions import current_user_id, session_required

from ..db.db import db_conn
from ..domain.rules import validate_notification_preferences_update
from ..notifications import (get_preferences, list_notifications as fetch_notifications, mark_all_read,
                             mark_read, unread_count, upsert_preferences)
from ..utils.pagination import build_pagination_meta, get_pagination_args
from ..utils.responses import fail, ok

notifications_bp = Blueprint("notifications", __name__, url_prefix="/api/notifications")


@notifications_bp.get("")
@session_required
def list_notifications():
    """La campana de quien llama, más nueva primero. Abrirla no marca nada como leído."""
    user_id = current_user_id()
    page, per_page, offset = get_pagination_args(request)
    with db_conn() as conn:
        total, items = fetch_notifications(conn, user_id, limit=per_page, offset=offset)
    return ok(data=items, message="Notificaciones", pagination=build_pagination_meta(page, per_page, total))


@notifications_bp.get("/unread-count")
@session_required
def unread_notification_count():
    user_id = current_user_id()
    with db_conn() as conn:
        unread = unread_count(conn, user_id)
    return ok(data={"unread": unread})


@notifications_bp.patch("/<int:notification_id>/read")
@session_required
def mark_notification_read(notification_id: int):
    user_id = current_user_id()
    with db_conn() as conn:
        row = mark_read(conn, user_id, notification_id)
    if not row:
        return fail("Notificación no encontrada", status=404)
    return ok(data=dict(row), message="Notificación leída")


@notifications_bp.post("/read-all")
@session_required
def mark_all_notifications_read():
    user_id = current_user_id()
    with db_conn() as conn:
        updated = mark_all_read(conn, user_id)
    return ok(data={"updated": updated}, message="Notificaciones leídas")


@notifications_bp.get("/preferences")
@session_required
def get_notification_preferences():
    user_id = current_user_id()
    with db_conn() as conn:
        prefs = get_preferences(conn, user_id)
    return ok(data=prefs)


@notifications_bp.patch("/preferences")
@session_required
def update_notification_preferences():
    user_id = current_user_id()
    try:
        updates = validate_notification_preferences_update(request.get_json(silent=True) or {})
    except ValueError as exc:
        return fail(str(exc), status=400)

    with db_conn() as conn:
        prefs = upsert_preferences(conn, user_id, updates)
    return ok(data=prefs, message="Preferencias actualizadas")
