from flask import Blueprint

from ..domain.rules import registration_mode
from ..utils.responses import ok

health_bp = Blueprint('health', __name__, url_prefix='/')


@health_bp.get('/')
def home():
    """Estado mínimo de la API sin exponer el mapa interno de rutas."""
    return ok(message='AlbumFP API disponible', data={'status': 'ok', 'health': '/api/health'})


@health_bp.get('/api/health')
def api_health():
    """Health check público apto para proxy inverso y frontend.

    `registration_mode` no es un secreto -- es una decisión de producto, no
    una credencial -- y el formulario de registro lo necesita ANTES de tener
    sesión para decidir qué mostrar (formulario normal, campo de invitación,
    o "registro cerrado"). El backend sigue siendo la autoridad real: esto
    solo evita que la pantalla muestre un formulario que el servidor va a
    rechazar de todos modos.
    """
    return ok(message='API disponible', data={'status': 'ok', 'registration_mode': registration_mode()})
