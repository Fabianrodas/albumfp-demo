"""
Registra todos los blueprints CRUD básicos.

"""
from ..api.health import health_bp
from ..api.auth import auth_bp
from ..api.albums import albums_bp
from ..api.shares import shares_bp
from ..api.media import media_bp
# Cuelga sus rutas del mismo `media_bp`, así que hay que importarlo ANTES de
# registrar el blueprint o esas URLs no existirían.
from ..api import media_context  # noqa: F401
from ..api import media_posters  # noqa: F401  (v1.1, mismo media_bp)
from ..api.places import places_bp
from ..api.tags import tags_bp
from ..api.users import users_bp
from ..api.notifications import notifications_bp
from ..api.home import home_bp
from ..api.smart_albums import smart_albums_bp
from ..api.export import export_bp
from ..api.passkeys import passkeys_bp
from ..api.comments import comments_bp

def register_blueprints(app):
    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(albums_bp)
    app.register_blueprint(shares_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(places_bp)
    app.register_blueprint(tags_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(home_bp)
    app.register_blueprint(smart_albums_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(passkeys_bp)
    app.register_blueprint(comments_bp)
