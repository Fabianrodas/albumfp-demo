import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / 'app' / 'api'


def decorator_name(node):
    if isinstance(node, ast.Call):
        return decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ''


def function_decorators(path: Path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    return {
        node.name: {decorator_name(item) for item in node.decorator_list}
        for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _todas_las_funciones_de_api():
    """Inventario de TODAS las funciones de nivel superior de app/api/, sean
    o no rutas. Borrar una debe obligar a tocar esta lista (ver
    test_authenticated_api_functions_require_a_session) o falla; añadir una
    fuera de un blueprint debe ser detectable aquí también."""
    nombres = set()
    for path in API.glob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        nombres.update(
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
    return nombres


class SecuritySurfaceTests(unittest.TestCase):
    def test_authenticated_api_functions_require_a_session(self):
        expected = {
            'auth.py': {'me', 'update_me', 'change_my_password', 'logout_user', 'logout_all_sessions',
                        'list_my_sessions', 'revoke_my_session', 'revoke_other_sessions',
                        'upload_my_avatar', 'my_stats', 'delete_me',
                        'recovery_code_status', 'generate_recovery_codes', 'revoke_recovery_codes'},
            'albums.py': {'list_albums', 'create_album', 'get_album',
                          'get_album_stats', 'update_album', 'delete_album',
                          'add_album_asset', 'remove_album_asset', 'list_album_activity'},
            'users.py': {'list_users', 'get_user_profile', 'list_user_public_albums'},
            'media.py': {'get_media_detail', 'search_media', 'list_library_media', 'list_recent_library_media', 'location_search', 'location_staticmap', 'read_media_file', 'read_media_preview', 'list_album_media', 'download_album', 'list_public_media', 'create_media', 'update_media', 'delete_media', 'toggle_favorite', 'set_media_archived', 'list_trash', 'list_favorites', 'restore_media', 'delete_media_permanently', 'restore_all_trash', 'delete_all_trash'},
            # Cuelgan del mismo media_bp pero viven en su propio fichero.
            'media_context.py': {'get_media_context', 'enrich_media_location', 'enrich_media_solar', 'enrich_media_holiday', 'enrich_media_context', 'get_media_ocr', 'detect_media_ocr', 'delete_media_ocr', 'suggest_media_tags'},
            'media_posters.py': {'set_video_poster'},
            'tags.py': {'list_tags', 'create_tag', 'assign_tags', 'unassign_tag'},
            'shares.py': {'list_album_shares', 'create_album_share', 'update_share_permission', 'claim_account_share', 'disable_share', 'regenerate_share_token', 'reveal_share_token'},
            'places.py': {'list_places', 'list_place_media'},
            'smart_albums.py': {'list_smart_albums', 'create_smart_album', 'get_smart_album',
                                'update_smart_album', 'delete_smart_album', 'list_smart_album_media'},
            'export.py': {'export_summary', 'download_export'},
            'comments.py': {'list_media_comments', 'create_media_comment', 'update_comment', 'delete_comment'},
            'passkeys.py': {'passkey_register_options', 'passkey_register_verify', 'list_passkeys',
                            'rename_passkey', 'delete_passkey'},
            'notifications.py': {'get_notification_preferences', 'update_notification_preferences',
                                 'list_notifications', 'unread_notification_count',
                                 'mark_notification_read', 'mark_all_notifications_read'},
        }
        missing = []
        for filename, functions in expected.items():
            decorators = function_decorators(API / filename)
            for function in functions:
                if 'session_required' not in decorators.get(function, set()):
                    missing.append(f'{filename}:{function}')
        self.assertEqual([], missing, f'Protected endpoints missing @session_required: {missing}')

    def test_every_non_public_route_has_an_authorization_boundary(self):
        public_or_separately_protected = {
            'home', 'api_health', 'register_user', 'login_user', 'recover_account',
            'passkey_login_options', 'passkey_login_verify',
            # F04: la lista de comentarios de un enlace público es de solo
            # lectura y la autoriza el token, igual que la foto.
            'read_shared_media_comments',
            # Las cinco del enlace público se autorizan por token (o por
            # contraseña + cookie de desbloqueo), no por sesión. Ni la vista
            # previa ni el detalle amplían nada: alcanzan exactamente la misma
            # media que el original y pasan por el mismo `get_token_share`, y
            # el detalle devuelve siempre role=read sin capacidades.
            'read_shared_media_by_token', 'read_shared_media_detail', 'read_shared_file',
            'read_shared_preview', 'unlock_shared_link',
            'purge_expired_trash_global', 'purge_expired_rate_limits', 'purge_expired_share_unlocks',
            'create_registration_invite',
            # Foto de perfil pública: una etiqueta <img> nunca llevaría el token,
            # y es la misma imagen que ya se ve en el directorio de usuarios.
            'read_user_avatar',
        }
        missing = []
        route_methods = {'get', 'post', 'put', 'patch', 'delete'}
        for path in API.glob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                decorators = {decorator_name(item) for item in node.decorator_list}
                is_route = bool(decorators & route_methods)
                if not is_route or node.name in public_or_separately_protected:
                    continue
                if 'session_required' not in decorators:
                    missing.append(f'{path.name}:{node.name}')
        self.assertEqual([], missing, f'Routes without a session or explicit public boundary: {missing}')

    def test_public_share_functions_do_not_accidentally_require_a_user_session(self):
        public_share = {'read_shared_media_by_token', 'read_shared_media_detail', 'read_shared_file',
                        'read_shared_preview', 'unlock_shared_link'}
        decorators = function_decorators(API / 'shares.py')
        unexpected = [name for name in public_share if 'session_required' in decorators.get(name, set())]
        self.assertEqual([], unexpected)

    def test_share_claim_is_authenticated_and_public_links_are_read_only(self):
        source = (API / 'shares.py').read_text(encoding='utf-8')
        self.assertIn('@shares_bp.post("/shared/<string:token>/claim")', source)
        self.assertIn('share_type', source)
        self.assertIn('public_link', source)
        self.assertNotIn('@shares_bp.post("/shared/<string:token>/media")', source)
        self.assertNotIn('@shares_bp.delete("/shared/<string:token>/media/<int:media_id>")', source)


    def test_public_media_detail_is_read_only_and_respects_show_metadata(self):
        """El detalle de un enlace reutiliza la pantalla autenticada, asi que su
        unica defensa contra pintar botones de escritura es que el servidor
        mande siempre rol de lectura y cero capacidades."""
        source = (API / 'shares.py').read_text(encoding='utf-8')
        detalle = source[source.index('def read_shared_media_detail'):source.index('def regenerate_share_token')]
        self.assertIn('"album_role": "read"', detalle)
        self.assertIn('"album_capabilities": []', detalle)
        # Nunca debe conceder escritura ni mirar capacidades del que pide.
        self.assertNotIn('require_album_capability', detalle)
        self.assertNotIn("'write'", detalle)
        # `show_metadata` apagado tiene que dejar fuera EXIF, lugar, tags y OCR.
        self.assertIn('if share["show_metadata"]:', detalle)
        # Se recorta el bloque de imports del principio: ahi aparecen los
        # nombres de los helpers sin ser todavia una llamada.
        cuerpo = detalle[detalle.index('with db_conn()'):]
        posicion_gate = cuerpo.index('if share["show_metadata"]:')
        for campo in ('_read_exif(', '_read_context(', '_read_ocr(', 'media_tags'):
            # TODAS las apariciones deben caer despues del gate: comprobar solo
            # la ultima dejaba pasar una copia extra colada antes (probado
            # mutando el fuente a proposito).
            self.assertGreater(cuerpo.index(campo), posicion_gate,
                               f'{campo} debe quedar DENTRO del gate de show_metadata')
            self.assertEqual(1, cuerpo.count(campo),
                             f'{campo} debe leerse una sola vez, dentro del gate')
        # Solo se sirve media viva y del album del enlace.
        self.assertIn('{in_album_sql("album_id")}', detalle)
        self.assertIn('m.deleted_at IS NULL', detalle)

    def test_regenerating_a_link_stores_only_the_hash(self):
        source = (API / 'shares.py').read_text(encoding='utf-8')
        regenerar = source[source.index('def regenerate_share_token'):source.index('def _shared_media_file')]
        self.assertIn('require_album_permission', regenerar)
        self.assertIn('"owner"', regenerar)
        self.assertIn('hash_token(token)', regenerar)
        # El token crudo solo puede viajar en la respuesta, nunca a la base.
        self.assertNotIn('SET token_hash = :token,', regenerar)

    def test_public_albums_grant_read_only_and_never_expose_trash(self):
        source = (ROOT / 'app' / 'security' / 'permissions.py').read_text(encoding='utf-8')
        # Un álbum no privado concede lectura a cualquier cuenta, nunca write ni
        # owner, y ni una sola capacidad de escritura.
        self.assertIn('if not album["is_private"]:', source)
        self.assertIn('{"role": "read", "capabilities": set(), "album": album}', source)
        self.assertNotIn('{"role": "write"', source)

        media = (API / 'media.py').read_text(encoding='utf-8')
        # La media en papelera sigue exigiendo ser dueño aunque el álbum sea público.
        self.assertIn('required = "owner" if media["deleted_at"] is not None else "read"', media)
        # El listado del álbum nunca devuelve media borrada.
        self.assertIn('"m.deleted_at IS NULL"', media)

    def test_shared_album_listing_only_uses_account_shares(self):
        source = (API / 'albums.py').read_text(encoding='utf-8')
        self.assertIn("sh.share_type = 'account'", source)

    def test_public_health_endpoint_does_not_enumerate_application_routes(self):
        source = (API / 'health.py').read_text(encoding='utf-8')
        self.assertNotIn('iter_rules()', source, 'Public health routes must not disclose the whole route map')

    def test_there_is_no_internal_maintenance_endpoint_left_to_authenticate(self):
        """Antes esto comprobaba que el blueprint interno comparase su token en
        tiempo constante. Desde S12 ese blueprint no existe: el mantenimiento
        son comandos de consola, asi que no hay ningun token que comparar ni
        ninguna ruta privilegiada que proteger. La forma mas fuerte de la
        prueba anterior es que el fichero no este."""
        self.assertFalse((API / 'jobs.py').exists())
        for path in API.glob('*.py'):
            with self.subTest(modulo=path.name):
                self.assertNotIn('X-Internal-Token', path.read_text(encoding='utf-8'))

    def test_trash_destructive_routes_are_owner_scoped_and_remove_files(self):
        source = (API / 'media.py').read_text(encoding='utf-8')
        self.assertIn('@media_bp.delete("/media/<int:media_id>/permanent")', source)
        self.assertIn('@media_bp.post("/trash/restore-all")', source)
        self.assertIn('@media_bp.delete("/trash")', source)
        # Cada una de las tres rutas acota al dueño del asset (L10A: ya no hay
        # JOIN a albums; el dueño del asset es el de sus albumes).
        for nombre in ('delete_media_permanently', 'restore_all_trash', 'delete_all_trash'):
            inicio = source.index(f'def {nombre}(')
            fin = source.find('\n@', inicio)
            cuerpo = source[inicio:fin if fin != -1 else None]
            with self.subTest(ruta=nombre):
                self.assertIn('m.user_id = :user_id', cuerpo)
                self.assertIn('{ACTIVE_SCOPE_SQL}', cuerpo)
                self.assertIn('m.deleted_at IS NOT NULL', cuerpo)
        # Las dos rutas destructivas borran archivos por el mismo sitio, y ese
        # sitio se lleva TAMBIEN la vista previa: si alguien vuelve a llamar a
        # `remove_stored_file` suelto aqui, la vista previa quedaria huerfana
        # en disco despues de vaciar la papelera. `remove_media_files` ahora
        # devuelve (borrados, pendientes) -- un fallo de infraestructura al
        # borrar cuenta como pendiente, no como "no habia nada" (spec S7).
        self.assertIn('remove_media_files(deleted)', source)
        self.assertIn('borrados, pendientes = remove_media_files(deleted)', source)
        self.assertIn('item.get("preview_storage_path")', source)

    def test_private_api_responses_disable_browser_caching(self):
        source = (ROOT / 'application.py').read_text(encoding='utf-8')
        self.assertIn('Cache-Control', source)
        self.assertIn('no-store', source)
        self.assertIn('Cross-Origin-Resource-Policy', source)
        # La credencial va en cookie desde S01: variar por `Authorization` ya
        # no separa nada y dejaria una cache compartida sirviendo la respuesta
        # de una sesion a otra.
        self.assertIn('"Vary", "Cookie"', source)
        self.assertNotIn('"Vary", "Authorization"', source)

    def test_storage_maintenance_never_becomes_an_http_route(self):
        """El GC, el reconciliador y la resolución de key_hash son comandos de
        consola a propósito: no hay ninguna razón para que sean alcanzables
        desde la red, ni siquiera con el token de jobs."""
        rutas = _todas_las_funciones_de_api()
        for prohibida in ('cleanup_workspace', 'reconcile_storage_operations',
                          'storage_key_lookup', 'check_storage', 'cleanup_orphaned_media',
                          'backfill_media_checksums', 'verify_media_integrity'):
            with self.subTest(prohibida=prohibida):
                self.assertNotIn(prohibida, rutas)


if __name__ == '__main__':
    unittest.main()
