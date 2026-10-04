"""S16 — la matriz de autorización: cada ruta declarada, ninguna por accidente.

Estas pruebas no abren la base de datos. Es el patrón establecido del proyecto
(ver `test_security_surface.py` y `test_tag_isolation.py`): la autorización se
fija leyendo el AST de `app/api/`, y los endpoints se ejercitan de verdad en la
QA en vivo contra una base desechable.

Lo que hace útil a esta matriz no es repetir lo que el código ya dice, sino que
**la tabla y el código tienen que coincidir**. Si alguien cambia la capacidad que
exige una ruta, o añade una ruta nueva, o quita `session_required`, la prueba
falla nombrando exactamente qué se movió. Una tabla que solo se leyera a sí misma
no valdría nada.
"""

import ast
import unittest
from pathlib import Path

from app.media.assets import in_active_album_sql

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "app/api"

VERBS = ("get", "post", "put", "patch", "delete")

# Cómo se autoriza cada ruta. `auth` es la puerta de sesión; `gate` es lo que
# decide el acceso al recurso concreto.
#
#   session / public            -- ¿exige cookie de sesión?
#   cap:<x>                     -- require_album_capability(..., "<x>") o, sobre
#                                  un asset (L10A), require_asset_capability(...)
#   collab                      -- require_album_collaborator(...): dueño o
#                                  acceso de cuenta activo (L12)
#   perm:<x>                    -- require_album_permission(..., "<x>") o
#                                  require_asset_permission(..., "<x>")
#   owner-sql                   -- el propio WHERE limita a las filas del dueño
#   self                        -- actúa solo sobre la cuenta que llama
#   share-token                 -- autoriza el token del enlace, no la sesión
#   public-feed                 -- muestra media de cualquiera, pero solo de
#                                  albumes no privados y activos
#   none                        -- deliberadamente sin recurso que proteger
ROUTES = {
    # --- albums.py ---
    "list_albums": ("session", "owner-sql"),
    "create_album": ("session", "self"),
    "get_album": ("session", "perm:read"),
    "get_album_stats": ("session", "perm:read"),
    # L12: el historial de colaboración es del dueño y de quien tiene un acceso
    # de cuenta activo -- NO de cualquiera que pueda leer un álbum público.
    "list_album_activity": ("session", "collab"),
    "update_album": ("session", "cap:edit_album"),
    "delete_album": ("session", "perm:owner"),
    # L10B: gestionar pertenencias es solo del dueño; ninguna capacidad llega.
    "add_album_asset": ("session", "perm:owner"),
    "remove_album_asset": ("session", "perm:owner"),
    # --- auth.py ---
    "register_user": ("public", "none"),
    "login_user": ("public", "none"),
    # L14: sin sesión por definición (se perdió la contraseña). Lo protegen el
    # cupo compartido con el login y un código de 150 bits de un solo uso.
    "recover_account": ("public", "none"),
    "recovery_code_status": ("session", "self"),
    "generate_recovery_codes": ("session", "self"),
    "revoke_recovery_codes": ("session", "self"),
    "logout_user": ("session", "self"),
    "logout_all_sessions": ("session", "self"),
    "list_my_sessions": ("session", "self"),
    "revoke_my_session": ("session", "self"),
    "revoke_other_sessions": ("session", "self"),
    "me": ("session", "self"),
    "update_me": ("session", "self"),
    "change_my_password": ("session", "self"),
    "upload_my_avatar": ("session", "self"),
    "my_stats": ("session", "self"),
    "delete_me": ("session", "self"),
    # --- health.py ---
    "home": ("public", "none"),
    "api_health": ("public", "none"),
    # --- home.py ---
    "personal_home": ("session", "owner-sql"),
    # --- media.py ---
    "get_media_detail": ("session", "perm:read"),
    "list_album_media": ("session", "perm:read"),
    "download_album": ("session", "perm:read"),
    "create_media": ("session", "cap:upload"),
    "update_media": ("session", "cap:edit_media"),
    "delete_media": ("session", "cap:delete_media"),
    "toggle_favorite": ("session", "cap:organize"),
    "set_media_archived": ("session", "cap:organize"),
    "read_media_file": ("session", "owner-sql"),
    "read_media_preview": ("session", "owner-sql"),
    "restore_media": ("session", "perm:owner"),
    "list_trash": ("session", "owner-sql"),
    "delete_media_permanently": ("session", "owner-sql"),
    "restore_all_trash": ("session", "owner-sql"),
    "delete_all_trash": ("session", "owner-sql"),
    "search_media": ("session", "owner-sql"),
    "list_library_media": ("session", "owner-sql"),
    "list_recent_library_media": ("session", "owner-sql"),
    "list_favorites": ("session", "owner-sql"),
    # El muro publico de /inicio: ensena media de TODAS las cuentas a
    # proposito. Su frontera no es el dueno, es que el album sea publico.
    "list_public_media": ("session", "public-feed"),
    "location_search": ("session", "none"),
    "location_staticmap": ("session", "none"),
    # --- media_context.py ---
    "get_media_context": ("session", "perm:read"),
    "get_media_ocr": ("session", "perm:read"),
    "delete_media_ocr": ("session", "perm:owner"),
    "enrich_media_location": ("session", "cap:edit_media"),
    "enrich_media_solar": ("session", "cap:edit_media"),
    "enrich_media_holiday": ("session", "cap:edit_media"),
    "enrich_media_context": ("session", "cap:edit_media"),
    # Los dos únicos que sacan PÍXELES fuera del servidor: dueño real, nunca
    # una capacidad. Un colaborador con las cinco puede editarlo todo, pero que
    # la imagen viaje a un tercero lo decide quien creó el álbum.
    "detect_media_ocr": ("session", "owner-sql"),
    "suggest_media_tags": ("session", "owner-sql"),
    # --- export.py (L13) --- solo la biblioteca de quien llama
    "export_summary": ("session", "self"),
    "download_export": ("session", "self"),
    # --- comments.py (F04) --- leer/comentar = acceso de lectura central al
    # asset; editar/borrar = autor (SQL con user_id); la lista pública la
    # autoriza el token del enlace, como la foto.
    "list_media_comments": ("session", "owner-sql"),
    "create_media_comment": ("session", "owner-sql"),
    "update_comment": ("session", "owner-sql"),
    "delete_comment": ("session", "owner-sql"),
    "read_shared_media_comments": ("public", "share-token"),
    # --- passkeys.py (L15) --- el login es público por definición (cupo de
    # login, challenge de un solo uso); registrar y gestionar son de la cuenta.
    "passkey_login_options": ("public", "none"),
    "passkey_login_verify": ("public", "none"),
    "passkey_register_options": ("session", "self"),
    "passkey_register_verify": ("session", "self"),
    "list_passkeys": ("session", "owner-sql"),
    "rename_passkey": ("session", "owner-sql"),
    "delete_passkey": ("session", "owner-sql"),
    # --- notifications.py ---
    "get_notification_preferences": ("session", "self"),
    "update_notification_preferences": ("session", "self"),
    "list_notifications": ("session", "owner-sql"),
    "unread_notification_count": ("session", "owner-sql"),
    "mark_notification_read": ("session", "owner-sql"),
    "mark_all_notifications_read": ("session", "owner-sql"),
    # --- places.py ---
    "list_places": ("session", "owner-sql"),
    "list_place_media": ("session", "owner-sql"),
    # --- shares.py ---
    "list_album_shares": ("session", "perm:owner"),
    "create_album_share": ("session", "perm:owner"),
    "regenerate_share_token": ("session", "perm:owner"),
    "reveal_share_token": ("session", "perm:owner"),
    "update_share_permission": ("session", "owner-sql"),
    "disable_share": ("session", "owner-sql"),
    "claim_account_share": ("session", "share-token"),
    "unlock_shared_link": ("public", "share-token"),
    "read_shared_media_by_token": ("public", "share-token"),
    "read_shared_media_detail": ("public", "share-token"),
    "read_shared_file": ("public", "share-token"),
    "read_shared_preview": ("public", "share-token"),
    # --- smart_albums.py ---
    # L11: una búsqueda guardada es del dueño y de nadie más. Ninguna capacidad
    # de colaborador ni enlace público llega: cada consulta filtra por dueño.
    "list_smart_albums": ("session", "owner-sql"),
    "create_smart_album": ("session", "owner-sql"),
    "get_smart_album": ("session", "owner-sql"),
    "update_smart_album": ("session", "owner-sql"),
    "delete_smart_album": ("session", "owner-sql"),
    "list_smart_album_media": ("session", "owner-sql"),
    # --- tags.py ---
    "list_tags": ("session", "owner-sql"),
    "create_tag": ("session", "cap:organize"),
    "assign_tags": ("session", "cap:organize"),
    "unassign_tag": ("session", "cap:organize"),
    # --- users.py ---
    "list_users": ("session", "none"),
    "get_user_profile": ("session", "none"),
    "list_user_public_albums": ("session", "none"),
    # Deliberadamente público: un `<img>` nunca pasa por el interceptor, así
    # que jamás podría mandar la credencial.
    "read_user_avatar": ("public", "none"),
}

# Las ÚNICAS rutas sin sesión. Añadir una nueva tiene que ser una decisión
# explícita, no el resultado de olvidar un decorador.
PUBLIC_ROUTES = {name for name, (auth, _) in ROUTES.items() if auth == "public"}


class _Gates(ast.NodeVisitor):
    """Extrae la capacidad o el permiso que una función exige de verdad."""

    def __init__(self):
        self.gates = set()

    def visit_Call(self, node):
        nombre = None
        if isinstance(node.func, ast.Name):
            nombre = node.func.id
        elif isinstance(node.func, ast.Attribute):
            nombre = node.func.attr
        if nombre == "require_album_collaborator":
            self.gates.add("collab")
        if nombre in ("require_album_capability", "require_album_permission",
                      "require_asset_capability", "require_asset_permission"):
            # El último argumento posicional constante es la capacidad/permiso;
            # los anteriores son la conexión y los ids. Ojo: hay llamadas con
            # `media["album_id"]` en medio, y esa cadena NO es el permiso.
            constantes = [a.value for a in node.args
                          if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if constantes:
                prefijo = "cap" if "capability" in nombre else "perm"
                self.gates.add(f"{prefijo}:{constantes[-1]}")
        self.generic_visit(node)


def _routes():
    """{funcion: (modulo, decoradores, gates, fuente)} para cada ruta real."""
    encontrado = {}
    for archivo in sorted(API.glob("*.py")):
        fuente = archivo.read_text(encoding="utf-8")
        arbol = ast.parse(fuente)
        for nodo in arbol.body:
            if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            rutas, decoradores = [], set()
            for dec in nodo.decorator_list:
                if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr in VERBS):
                    rutas.append(dec.func.attr)
                elif isinstance(dec, ast.Name):
                    decoradores.add(dec.id)
            if not rutas:
                continue
            visitante = _Gates()
            visitante.visit(nodo)
            encontrado[nodo.name] = (
                archivo.name, decoradores, visitante.gates,
                ast.get_source_segment(fuente, nodo) or "",
            )
    return encontrado


class RouteInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rutas = _routes()

    def test_every_route_is_classified_in_the_matrix(self):
        """Una ruta nueva sin entrada aquí falla. Ese es el punto entero: obliga
        a decidir su modelo de autorización en vez de heredarlo por descuido."""
        sin_clasificar = sorted(set(self.rutas) - set(ROUTES))
        self.assertEqual([], sin_clasificar,
                         f"rutas sin clasificar en la matriz: {sin_clasificar}")

    def test_the_matrix_lists_no_route_that_stopped_existing(self):
        """Al revés: una entrada huérfana da una falsa sensación de cobertura."""
        fantasmas = sorted(set(ROUTES) - set(self.rutas))
        self.assertEqual([], fantasmas, f"entradas sin ruta real: {fantasmas}")

    def test_the_declared_session_requirement_matches_the_decorator(self):
        for nombre, (auth, _gate) in ROUTES.items():
            if nombre not in self.rutas:
                continue
            _modulo, decoradores, _gates, _fuente = self.rutas[nombre]
            with self.subTest(ruta=nombre):
                if auth == "session":
                    self.assertIn("session_required", decoradores,
                                  f"{nombre} se declara con sesión pero no la exige")
                else:
                    self.assertNotIn("session_required", decoradores,
                                     f"{nombre} se declara pública pero exige sesión")

    def test_the_set_of_public_routes_never_grows_by_accident(self):
        """Las catorce rutas sin sesión son todas deliberadas: registro, login,
        recuperar con código (L14) y las dos mitades del login con passkey
        (L15; por definición), salud, el avatar (un `<img>` no puede mandar la
        credencial) y las seis de un enlace público (F04 añade leer sus
        comentarios), que autoriza el token."""
        reales = {n for n, (_m, d, _g, _s) in self.rutas.items() if "session_required" not in d}
        self.assertEqual(PUBLIC_ROUTES, reales,
                         "cambió el conjunto de rutas públicas; si es a propósito, actualiza "
                         "la matriz y explica por qué en el commit")

    def test_the_declared_capability_matches_the_one_the_code_demands(self):
        """El que de verdad ataja regresiones: si alguien baja `edit_album` a
        `organize`, o sube `upload` a `owner`, la tabla deja de cuadrar."""
        for nombre, (_auth, gate) in ROUTES.items():
            if nombre not in self.rutas or not gate.startswith(("cap:", "perm:", "collab")):
                continue
            _modulo, _dec, gates, _fuente = self.rutas[nombre]
            with self.subTest(ruta=nombre):
                self.assertIn(gate, gates,
                              f"{nombre} declara {gate} pero el código exige {sorted(gates) or 'nada'}")


class OwnerScopedRouteTests(unittest.TestCase):
    """Las rutas marcadas `owner-sql` no llaman a ninguna función de permisos:
    su propio `WHERE` es la frontera. Si ese `WHERE` desaparece, la ruta pasa a
    devolver filas de cualquiera y ninguna prueba de permisos lo notaría."""

    @classmethod
    def setUpClass(cls):
        cls.rutas = _routes()

    def test_an_owner_scoped_route_filters_by_the_calling_user(self):
        for nombre, (_auth, gate) in ROUTES.items():
            if gate != "owner-sql" or nombre not in self.rutas:
                continue
            _modulo, _dec, _gates, fuente = self.rutas[nombre]
            with self.subTest(ruta=nombre):
                self.assertTrue(
                    ":user_id" in fuente or "user_id=user_id" in fuente
                    or "current_user_id" in fuente or "_owned_image_for_external_call" in fuente,
                    f"{nombre} se declara acotada al dueño pero no nombra al usuario que llama",
                )

    def test_the_two_routes_that_send_pixels_to_a_third_party_demand_real_ownership(self):
        """Un colaborador con las cinco capacidades edita todo el contenido, pero
        que la foto salga del servidor lo decide quien creó el álbum. Las dos
        pasan por el mismo guardián."""
        for nombre in ("detect_media_ocr", "suggest_media_tags"):
            with self.subTest(ruta=nombre):
                _modulo, _dec, _gates, fuente = self.rutas[nombre]
                self.assertIn("_owned_image_for_external_call", fuente)


class ShareTokenRouteTests(unittest.TestCase):
    """Un enlace público se resuelve por el HASH del token. Si alguna ruta
    volviera a buscar por el token en claro, un volcado de la base serviría
    para entrar -- que es justo lo que S08 cerró."""

    @classmethod
    def setUpClass(cls):
        cls.fuente = (API / "shares.py").read_text(encoding="utf-8")

    def test_no_query_looks_a_share_up_by_its_raw_token(self):
        self.assertNotIn("WHERE token =", self.fuente)
        self.assertNotIn("token = :token", self.fuente)

    def test_the_lookup_goes_through_the_hashed_column(self):
        self.assertIn("token_hash", self.fuente)


class SqlInjectionSurfaceTests(unittest.TestCase):
    """Ninguna sentencia esquiva `execute_safe`.

    `execute_safe` liga TODOS los valores como parametros de SQLAlchemy, valida
    los nombres de parametro, y rechaza comentarios y multi-sentencia. Varias
    consultas se arman con f-strings, y eso esta bien: lo que interpolan son
    fragmentos fijos escritos en el propio codigo (`WHERE` compuesto, lista de
    columnas a actualizar), nunca datos de la peticion, que siguen viajando como
    `:parametro`. Prohibir el f-string no haria nada por la seguridad y
    empujaria a rodear el helper, que es lo unico que si importa.
    """

    def test_no_module_executes_sql_without_going_through_execute_safe(self):
        culpables = []
        for archivo in sorted((ROOT / "app").rglob("*.py")):
            if archivo.name == "sql_security.py":
                continue
            for numero, linea in enumerate(archivo.read_text(encoding="utf-8").splitlines(), 1):
                if "conn.execute(" in linea or ".execute(text(" in linea:
                    culpables.append(f"{archivo.relative_to(ROOT)}:{numero}")
        self.assertEqual([], culpables,
                         f"SQL ejecutado sin execute_safe: {culpables}")

    def test_the_helper_still_binds_values_and_refuses_stacked_statements(self):
        """Si alguien relaja el helper, todo lo de arriba deja de significar
        nada -- por eso se comprueba aqui y no solo en su propio modulo."""
        from app.utils.sql_security import execute_safe, safe_text, sanitize_params

        with self.assertRaises(ValueError):
            safe_text("SELECT 1; DROP TABLE users")
        with self.assertRaises(ValueError):
            safe_text("SELECT 1 -- comentario")
        with self.assertRaises(ValueError):
            sanitize_params({"mal nombre": 1})
        self.assertTrue(callable(execute_safe))


class PublicFeedTests(unittest.TestCase):
    """El muro de /inicio ensena media de otras cuentas a proposito. Lo que lo
    hace seguro no es el dueno sino el estado del album: si desapareciera el
    filtro de privacidad, publicaria fotos privadas de todo el mundo."""

    @classmethod
    def setUpClass(cls):
        cls.rutas = _routes()

    def test_the_public_feed_only_reaches_public_active_albums(self):
        _modulo, _dec, _gates, fuente = self.rutas["list_public_media"]
        # L10A: el asset cuenta si esta en algun album publico y activo; el
        # `ia.active = TRUE` lo pone siempre el helper, y el album con el que se
        # presenta tambien tiene que ser publico.
        self.assertIn('in_active_album_sql("AND ia.is_private = FALSE")', fuente)
        self.assertIn("ia.active = TRUE", in_active_album_sql())
        self.assertIn('context_album_join(extra="AND ca.is_private = FALSE")', fuente)
        self.assertIn("m.deleted_at IS NULL", fuente)


if __name__ == "__main__":
    unittest.main()
