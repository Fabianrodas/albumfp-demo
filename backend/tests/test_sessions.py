"""S01 — sesiones opacas, cookie HttpOnly y CSRF.

Sin Postgres (el patron de este repo): lo que se ejercita de verdad es el
decorador, con `db_conn`/`load_session` sustituidos. El camino contra una base
real se verifica en QA en vivo.
"""
import os
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import patch

from flask import Flask

from app.security import sessions


@contextmanager
def _sin_base():
    yield object()


def _app_con_ruta(metodo="POST"):
    app = Flask(__name__)

    @app.route("/probar", methods=[metodo])
    @sessions.session_required
    def probar():
        return {"user": sessions.current_user_id()}, 200

    return app


def _fila(csrf="secreto-csrf", user_id=7, session_id=3):
    return {
        "id": session_id,
        "user_id": user_id,
        "csrf_hash": sessions.hash_token(csrf),
        "expires_at": datetime.now() + timedelta(days=1),
        "remember_me": False,
    }


class TokenHashingTests(unittest.TestCase):
    def test_the_stored_value_is_a_sha256_hex_not_the_token(self):
        crudo = "un-token-cualquiera"
        guardado = sessions.hash_token(crudo)
        self.assertEqual(64, len(guardado))
        self.assertNotIn(crudo, guardado)
        self.assertEqual(guardado, sessions.hash_token(crudo))

    def test_a_leaked_hash_cannot_be_replayed_as_the_cookie(self):
        """Robar la base no basta: el valor guardado, usado como cookie, se
        vuelve a hashear y ya no coincide con nada."""
        crudo = "token-de-verdad"
        guardado = sessions.hash_token(crudo)
        self.assertNotEqual(guardado, sessions.hash_token(guardado))

    def test_the_token_has_at_least_256_bits_of_entropy(self):
        registrado = {}

        def falso_execute(conn, sql, params=None):
            registrado.update(params or {})
            return None

        with patch.object(sessions, "execute_safe", falso_execute):
            sesion = sessions.create_session(object(), 1, False, "curl/8")
        # token_urlsafe(32) son 32 bytes -> 43 caracteres base64url.
        self.assertGreaterEqual(len(sesion["session_token"]), 43)
        self.assertGreaterEqual(len(sesion["csrf_token"]), 43)
        self.assertNotEqual(sesion["session_token"], sesion["csrf_token"])
        # Y en la fila NO va ninguno de los dos en claro.
        self.assertNotIn(sesion["session_token"], registrado.values())
        self.assertNotIn(sesion["csrf_token"], registrado.values())
        self.assertEqual(sessions.hash_token(sesion["session_token"]), registrado["token_hash"])


class CookieContractTests(unittest.TestCase):
    def _cookies(self, entorno):
        app = Flask(__name__)
        with patch.dict(os.environ, entorno, clear=False):
            with app.test_request_context("/"):
                from flask import make_response
                respuesta = sessions.attach_session_cookies(
                    make_response("", 200),
                    {"session_token": "s", "csrf_token": "c", "expires_at": None, "remember": True},
                )
        return [v for k, v in respuesta.headers.items() if k == "Set-Cookie"]

    def test_production_uses_the_host_prefix_and_every_hardening_flag(self):
        cabeceras = self._cookies({"APP_ENV": "production", "HTTPS_ENABLED": "true"})
        sesion = next(c for c in cabeceras if c.startswith("__Host-albumfp_session"))
        self.assertIn("HttpOnly", sesion)
        self.assertIn("Secure", sesion)
        self.assertIn("SameSite=Strict", sesion)
        self.assertIn("Path=/", sesion)
        # `__Host-` prohibe Domain; mandarlo haria que el navegador la tirara.
        self.assertNotIn("Domain=", sesion)
        self.assertNotIn("SameSite=None", sesion)

    def test_development_drops_the_prefix_because_localhost_has_no_tls(self):
        cabeceras = self._cookies({"APP_ENV": "development", "HTTPS_ENABLED": "false"})
        sesion = next(c for c in cabeceras if c.startswith("albumfp_session"))
        self.assertNotIn("__Host-", sesion)
        self.assertNotIn("Secure", sesion)
        # Lo unico que se relaja es `Secure`: sigue siendo HttpOnly y Strict.
        self.assertIn("HttpOnly", sesion)
        self.assertIn("SameSite=Strict", sesion)

    def test_the_csrf_cookie_is_readable_by_scripts_and_the_session_one_is_not(self):
        cabeceras = self._cookies({"APP_ENV": "development", "HTTPS_ENABLED": "false"})
        csrf = next(c for c in cabeceras if c.startswith("albumfp_csrf"))
        self.assertNotIn("HttpOnly", csrf)
        sesion = next(c for c in cabeceras if c.startswith("albumfp_session"))
        self.assertIn("HttpOnly", sesion)

    def test_remember_me_only_changes_how_long_it_lives(self):
        corta = sessions._lifetime(False)
        larga = sessions._lifetime(True)
        self.assertLess(corta, larga)


class SessionRequiredTests(unittest.TestCase):
    def setUp(self):
        self.entorno = patch.dict(os.environ, {"APP_ENV": "development", "HTTPS_ENABLED": "false"})
        self.entorno.start()
        self.addCleanup(self.entorno.stop)
        self.parches = [
            patch.object(sessions, "db_conn", _sin_base),
            patch.object(sessions, "touch_session", lambda *a, **k: None),
        ]
        for p in self.parches:
            p.start()
            self.addCleanup(p.stop)

    def _pedir(self, metodo="POST", cookies=None, cabeceras=None, fila=None, base_url=None):
        app = _app_con_ruta(metodo)
        cliente = app.test_client()
        for nombre, valor in (cookies or {}).items():
            cliente.set_cookie(nombre, valor)
        with patch.object(sessions, "load_session", lambda conn, raw: fila):
            return cliente.open("/probar", method=metodo, headers=cabeceras or {}, base_url=base_url)

    def test_without_a_cookie_it_is_401(self):
        respuesta = self._pedir(fila=None)
        self.assertEqual(401, respuesta.status_code)

    def test_a_revoked_or_expired_session_is_401(self):
        # `load_session` filtra revocadas/caducadas en SQL, asi que devolver
        # None es exactamente lo que ve el decorador en ese caso.
        respuesta = self._pedir(cookies={"albumfp_session": "loquesea"}, fila=None)
        self.assertEqual(401, respuesta.status_code)

    def test_a_mutation_without_the_csrf_header_is_403(self):
        respuesta = self._pedir(cookies={"albumfp_session": "t"}, fila=_fila())
        self.assertEqual(403, respuesta.status_code)
        self.assertEqual("csrf_invalid", respuesta.get_json()["code"])

    def test_a_mutation_with_the_wrong_csrf_header_is_403(self):
        respuesta = self._pedir(
            cookies={"albumfp_session": "t"},
            cabeceras={"X-CSRF-Token": "el-de-otra-sesion"},
            fila=_fila(),
        )
        self.assertEqual(403, respuesta.status_code)

    def test_a_mutation_with_the_right_csrf_header_goes_through(self):
        respuesta = self._pedir(
            cookies={"albumfp_session": "t"},
            cabeceras={"X-CSRF-Token": "secreto-csrf"},
            fila=_fila(),
        )
        self.assertEqual(200, respuesta.status_code)
        self.assertEqual(7, respuesta.get_json()["user"])

    def test_a_read_does_not_need_the_csrf_header(self):
        """Un GET no cambia nada; exigirle CSRF romperia toda la app sin
        aportar nada."""
        respuesta = self._pedir(metodo="GET", cookies={"albumfp_session": "t"}, fila=_fila())
        self.assertEqual(200, respuesta.status_code)

    def test_a_cross_site_origin_is_rejected_even_with_a_valid_csrf_token(self):
        respuesta = self._pedir(
            cookies={"albumfp_session": "t"},
            cabeceras={"X-CSRF-Token": "secreto-csrf", "Origin": "https://sitio-malo.example"},
            fila=_fila(),
        )
        self.assertEqual(403, respuesta.status_code)
        self.assertEqual("csrf_origin", respuesta.get_json()["code"])

    def test_the_dev_proxy_topology_is_accepted_bug_reported_in_manual_qa(self):
        """`ng serve` usa `changeOrigin: true` (`proxy.dev.conf.json`), asi que
        Flask ve `Host: localhost:5000` mientras el navegador manda
        `Origin: http://localhost:4200`. La primera version comparaba Origin
        contra `request.host_url` y rechazaba TODA mutacion del flujo normal
        de desarrollo con 403 csrf_origin -- lo reporto el usuario probando
        S01 a mano, ningun test automatico lo habia cazado. Se reproduce aqui
        con `base_url` fijando el Host que ve Flask, distinto del Origin."""
        respuesta = self._pedir(
            cookies={"albumfp_session": "t"},
            cabeceras={"X-CSRF-Token": "secreto-csrf", "Origin": "http://localhost:4200"},
            fila=_fila(),
            base_url="http://localhost:5000",
        )
        self.assertEqual(200, respuesta.status_code)

    def test_an_origin_outside_cors_origins_is_still_rejected_through_the_proxy(self):
        respuesta = self._pedir(
            cookies={"albumfp_session": "t"},
            cabeceras={"X-CSRF-Token": "secreto-csrf", "Origin": "https://sitio-malo.example"},
            fila=_fila(),
            base_url="http://localhost:5000",
        )
        self.assertEqual(403, respuesta.status_code)
        self.assertEqual("csrf_origin", respuesta.get_json()["code"])

class ClearSessionCookiesTests(unittest.TestCase):
    def _borradas(self):
        from flask import make_response
        app = Flask(__name__)
        with app.test_request_context("/"):
            respuesta = sessions.clear_session_cookies(make_response("", 200))
        return [v for k, v in respuesta.headers.items() if k == "Set-Cookie"]

    def test_it_clears_all_four_name_variants(self):
        cabeceras = "\n".join(self._borradas())
        for nombre in ("albumfp_session", "albumfp_csrf", "__Host-albumfp_session", "__Host-albumfp_csrf"):
            self.assertIn(nombre, cabeceras)

    def test_the_host_prefixed_deletions_carry_secure_so_browsers_do_not_reject_them(self):
        """Sin `secure=True` en el propio borrado, Werkzeug manda un
        Set-Cookie de borrado SIN el atributo Secure para un nombre
        `__Host-`, y el navegador lo rechaza por prefijo invalido -- exactamente
        el aviso que se vio en la consola durante el QA manual."""
        for cabecera in self._borradas():
            if cabecera.startswith("__Host-"):
                self.assertIn("Secure", cabecera, cabecera)


class SessionManagementQueryTests(unittest.TestCase):
    """`list_sessions`/`revoke_owned_session`/`rotate_session` no abren
    Postgres (el patron de este repo): lo que se fija aqui es que el SQL
    nombra `user_id` en cada WHERE, que es la unica frontera contra que una
    cuenta toque la sesion de otra por adivinar un id correlativo."""

    def _sql_de(self, funcion):
        """El literal SQL de la funcion, no su docstring -- si no, mencionar
        `token_hash` para explicar por que NO se selecciona se contaria como
        si se estuviera seleccionando."""
        import ast
        import inspect
        arbol = ast.parse(inspect.getsource(funcion))
        nodo_funcion = arbol.body[0]
        cuerpo = nodo_funcion.body
        primero = cuerpo[0] if cuerpo else None
        nodo_docstring = (
            primero.value
            if isinstance(primero, ast.Expr) and isinstance(primero.value, ast.Constant)
            and isinstance(primero.value.value, str)
            else None
        )
        return "\n".join(
            n.value for n in ast.walk(nodo_funcion)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n is not nodo_docstring
        )

    def test_list_sessions_scopes_by_user_and_never_selects_secrets(self):
        fuente = self._sql_de(sessions.list_sessions)
        self.assertIn("user_id = :user_id", fuente)
        self.assertNotIn("token_hash", fuente)
        self.assertNotIn("csrf_hash", fuente)

    def test_list_sessions_only_returns_live_sessions(self):
        fuente = self._sql_de(sessions.list_sessions)
        self.assertIn("revoked_at IS NULL", fuente)
        self.assertIn("expires_at > NOW()", fuente)

    def test_revoke_owned_session_requires_the_row_to_belong_to_the_caller(self):
        fuente = self._sql_de(sessions.revoke_owned_session)
        self.assertIn("id = :id AND user_id = :user_id", fuente)

    def test_revoke_owned_session_reports_whether_anything_was_revoked(self):
        class ResultadoFalso:
            rowcount = 0

        with patch.object(sessions, "execute_safe", lambda *a, **k: ResultadoFalso()):
            self.assertFalse(sessions.revoke_owned_session(object(), 1, 999))

        ResultadoFalso.rowcount = 1
        with patch.object(sessions, "execute_safe", lambda *a, **k: ResultadoFalso()):
            self.assertTrue(sessions.revoke_owned_session(object(), 1, 3))

    def test_rotate_session_creates_a_new_row_and_revokes_the_old_one(self):
        llamadas = []

        def espia_execute(conn, sql, params=None):
            llamadas.append((sql, params))

            class Resultado:
                def mappings(self_inner):
                    return self_inner

                def first(self_inner):
                    return {"remember_me": True}

            return Resultado()

        with patch.object(sessions, "execute_safe", espia_execute):
            nueva = sessions.create_session(object(), 5, True, "curl/8")
            # `create_session` ya se probo aparte; aqui solo interesa que
            # `rotate_session` la LLAME con la preferencia de la fila vieja.
            with patch.object(sessions, "create_session", return_value=nueva) as crear:
                with patch.object(sessions, "revoke_session") as revocar:
                    resultado = sessions.rotate_session(object(), 42, 5, "curl/8")

        revocar.assert_called_once_with(unittest.mock.ANY, 42)
        crear.assert_called_once_with(unittest.mock.ANY, 5, True, "curl/8")
        self.assertEqual(nueva, resultado)

    def test_rotate_session_defaults_remember_to_false_when_the_old_row_is_gone(self):
        """Si la fila vieja ya no existe (revocada dos veces a la vez, por
        ejemplo), no se inventa "recuerdame": se asume el caso mas corto."""
        def sin_fila(conn, sql, params=None):
            class Resultado:
                def mappings(self_inner):
                    return self_inner

                def first(self_inner):
                    return None

            return Resultado()

        with patch.object(sessions, "execute_safe", sin_fila):
            with patch.object(sessions, "create_session") as crear:
                with patch.object(sessions, "revoke_session"):
                    sessions.rotate_session(object(), 42, 5, None)

        crear.assert_called_once_with(unittest.mock.ANY, 5, False, None)


class UserAgentTests(unittest.TestCase):
    def test_it_is_trimmed_to_fit_the_column(self):
        self.assertEqual(240, len(sessions.summarize_user_agent("x" * 500)))

    def test_an_empty_agent_becomes_null_instead_of_an_empty_string(self):
        self.assertIsNone(sessions.summarize_user_agent("   "))
        self.assertIsNone(sessions.summarize_user_agent(None))


if __name__ == "__main__":
    unittest.main()
