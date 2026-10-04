"""El spool del multipart no puede caer en /tmp sin control (spec §11).

Lo que se fija aquí no es solo "el archivo grande cae en el workspace", sino
las cuatro formas de terminar mal una subida —parseo roto, autorización
denegada, cliente que se corta, cuerpo por encima del tope— dejando el
presupuesto liberado y ningún archivo detrás. La contabilidad tiene que
ocurrir MIENTRAS los bytes entran: con `proxy_request_buffering off` y HTTP/1.1
al upstream no hay `Content-Length` que mirar antes de escribir.
"""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask, abort, request
from werkzeug.test import EnvironBuilder

from app.storage import request_streams
from app.storage.request_streams import BudgetedRequest, install_budgeted_requests
from app.storage.workspace import Workspace, local_workspace, workspace_root

UN_MIB = 1024 * 1024


class _BaseSpool(unittest.TestCase):
    """App mínima con la clase de request instalada y raíces desechables."""

    presupuesto_gb = "0"
    concurrencia = "4"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        raiz = Path(self._tmp.name)
        # Las cuatro raíces, no solo dos: `workspace_root()` comprueba que sean
        # disjuntas, y otro módulo de la suite pudo dejar valores reales en el
        # entorno al importar `application.py` (que sí hace `load_dotenv`).
        self._patch = patch.dict(os.environ, {
            "MEDIA_WORK_ROOT": str(raiz / "work"),
            "MEDIA_STATE_ROOT": str(raiz / "state"),
            "MEDIA_QUARANTINE_ROOT": str(raiz / "quarantine"),
            "MEDIA_STORAGE_ROOT": str(raiz / "media"),
            "MEDIA_STORAGE_BACKEND": "local",
            "MEDIA_LOCAL_TEMP_MAX_GB": self.presupuesto_gb,
            "MEDIA_LOCAL_MIN_FREE_GB": "0",
            "MEDIA_LOCAL_MAX_USAGE_PERCENT": "100",
            "MEDIA_WORK_MAX_CONCURRENT": self.concurrencia,
        }, clear=False)
        self._patch.start()
        self.addCleanup(self._patch.stop)

        self.app = Flask(__name__)
        install_budgeted_requests(self.app)

        @self.app.post("/subir")
        def subir():
            archivo = request.files["file"]
            nombre = getattr(archivo.stream, "name", "")
            return {"bytes": len(archivo.read()), "spool": str(nombre)}

        @self.app.post("/protegido")
        def protegido():
            # Toca los archivos ANTES de rechazar: el spool ya existe cuando la
            # autorización dice que no.
            request.files.get("file")
            abort(401)

    # -- utilidades -------------------------------------------------------

    def _cuerpo(self, datos, nombre="grande.bin"):
        builder = EnvironBuilder(method="POST", path="/subir",
                                 data={"file": (io.BytesIO(datos), nombre)})
        environ = builder.get_environ()
        cuerpo = environ["wsgi.input"].read()
        # El propio builder vuelca a un temporal por encima de su umbral; sin
        # cerrarlo la suite escupe ResourceWarning.
        builder.close()
        environ["wsgi.input"] = io.BytesIO(cuerpo)
        return environ, cuerpo

    def _environ(self, datos, ruta="/subir", nombre="grande.bin"):
        environ, _ = self._cuerpo(datos, nombre)
        environ["PATH_INFO"] = ruta
        return environ

    def _sin_longitud(self, environ, cuerpo=None):
        """El caso de producción: Nginx sin buffering, sin `Content-Length`."""
        if cuerpo is not None:
            environ["wsgi.input"] = io.BytesIO(cuerpo)
        environ.pop("CONTENT_LENGTH", None)
        environ["wsgi.input_terminated"] = True
        return environ

    def _correr(self, environ):
        estados = []
        trozos = list(self.app(environ, lambda estado, cabeceras: estados.append(estado)))
        return estados[0], b"".join(trozos)

    def _post(self, datos, nombre="grande.bin"):
        # Se le pasa el environ ya construido en vez de `client.post(data=...)`:
        # el cliente de pruebas deja abierto el temporal donde codifica el
        # multipart salvo que se cierre la respuesta, y eso llena la suite de
        # ResourceWarning. `_cuerpo()` ya cierra el suyo.
        return self.app.test_client().open(self._environ(datos, nombre=nombre))

    def _directorios(self):
        return sorted(p.name for p in workspace_root().iterdir() if p.is_dir())

    def _archivos_sueltos(self):
        return sorted(str(p) for p in workspace_root().rglob("*")
                      if p.is_file() and not p.name.startswith(".ledger"))

    def assertSinRastro(self):
        self.assertEqual([], self._directorios(), "quedó un workspace sin liberar")
        self.assertEqual([], self._archivos_sueltos(), "quedó un archivo spooleado")

    def assertPresupuestoLibre(self):
        """El presupuesto vuelve entero: si quedara reservado, esta subida
        —que cabe de sobra— sería rechazada."""
        with local_workspace("ingress") as espacio:
            espacio.reserve_bytes(512 * 1024)


class BudgetedRequestTests(_BaseSpool):
    def test_la_clase_de_request_queda_instalada(self):
        self.assertIs(BudgetedRequest, self.app.request_class)

    def test_una_subida_grande_se_spoolea_dentro_del_workspace(self):
        datos = b"x" * (2 * UN_MIB)
        cuerpo = self._post(datos).get_json()
        self.assertEqual(len(datos), cuerpo["bytes"])
        self.assertTrue(cuerpo["spool"], "el archivo grande debe haber tocado disco")
        self.assertIn(str(workspace_root()), cuerpo["spool"])

    def test_el_spool_no_sobrevive_a_la_peticion(self):
        ruta = Path(self._post(b"y" * (2 * UN_MIB)).get_json()["spool"])
        self.assertFalse(ruta.exists())
        self.assertSinRastro()

    def test_una_subida_pequena_no_toca_el_disco(self):
        cuerpo = self._post(b"hola", "chico.bin").get_json()
        self.assertEqual(4, cuerpo["bytes"])
        self.assertEqual("", cuerpo["spool"], "un archivo pequeño no debe tocar disco")
        self.assertSinRastro()

    def test_no_queda_ningun_residuo_en_el_workspace_tras_varias_subidas(self):
        for _ in range(3):
            self._post(b"z" * (2 * UN_MIB), "g.bin")
        self.assertSinRastro()

    def test_ninguna_rama_cae_en_el_spool_por_defecto_de_werkzeug(self):
        """`default_stream_factory` devuelve un `SpooledTemporaryFile` que
        vuelca a /tmp por encima de 500 KiB pase lo que pase. Ninguna de las
        dos ramas puede acabar ahí, ni siquiera la de los archivos pequeños."""
        def prohibido(*args, **kwargs):
            raise AssertionError("se llamó al spool por defecto de Werkzeug")

        with patch("werkzeug.wrappers.request.default_stream_factory", prohibido):
            self.assertEqual(4, self._post(b"hola", "chico.bin").get_json()["bytes"])
            self.assertEqual(2 * UN_MIB, self._post(b"j" * (2 * UN_MIB)).get_json()["bytes"])
        self.assertSinRastro()

    def test_sin_content_length_nunca_se_usa_la_rama_en_memoria(self):
        with self.app.test_request_context("/subir", method="POST"):
            pequeno = request._get_file_stream(1024, "image/jpeg", "a.jpg")
            self.assertIsInstance(pequeno, io.BytesIO)
            # Sin `Content-Length` no se sabe el tamaño: se spoolea igual.
            grande = request._get_file_stream(None, "image/jpeg", "b.jpg")
            self.assertNotIsInstance(grande, io.BytesIO)
            self.assertIn(str(workspace_root()), str(grande.name))
            # Idempotente: el teardown volverá a llamarlo al salir del `with`.
            request._close_spools()
            self.assertFalse(Path(grande.name).exists())
        self.assertSinRastro()


class ReservaPorBloquesTests(_BaseSpool):
    """Spec §11: se reserva cada bloque ANTES de escribirlo."""

    presupuesto_gb = "0.001"  # ~1 MiB

    def _contando(self):
        llamadas = []
        original = Workspace.reserve_bytes

        def envoltorio(propio, n):
            llamadas.append(n)
            return original(propio, n)

        parche = patch.object(Workspace, "reserve_bytes", envoltorio)
        parche.start()
        self.addCleanup(parche.stop)
        return llamadas

    def test_sin_content_length_se_reserva_bloque_a_bloque(self):
        llamadas = self._contando()
        with patch.object(request_streams, "_RESERVE_BLOCK_BYTES", 128 * 1024):
            environ = self._environ(b"c" * (4 * UN_MIB))
            estado, _ = self._correr(self._sin_longitud(environ))
        self.assertTrue(estado.startswith("507"), estado)
        self.assertGreater(len(llamadas), 1,
                           "sin Content-Length la reserva tiene que ser por bloques")
        self.assertSinRastro()
        self.assertPresupuestoLibre()

    def test_con_content_length_se_rechaza_antes_de_escribir_un_byte(self):
        llamadas = self._contando()
        respuesta = self._post(b"c" * (4 * UN_MIB))
        self.assertEqual(507, respuesta.status_code)
        self.assertEqual("workspace_capacity_exceeded", respuesta.get_json()["code"])
        self.assertEqual(1, len(llamadas),
                         "un Content-Length que no cabe se rechaza sin escribir nada")
        self.assertSinRastro()
        self.assertPresupuestoLibre()

    def test_el_presupuesto_vuelve_tras_una_subida_rechazada(self):
        self._post(b"c" * (4 * UN_MIB))
        respuesta = self._post(b"d" * (600 * 1024), "cabe.bin")
        self.assertEqual(200, respuesta.status_code)
        self.assertEqual(600 * 1024, respuesta.get_json()["bytes"])
        self.assertSinRastro()


class FallosQueDejanBasuraTests(_BaseSpool):
    """Los cuatro finales malos: parseo, autorización, corte y tamaño."""

    def test_un_multipart_truncado_no_llega_a_request_files_y_no_deja_spool(self):
        """El caso que rompe limpiar mirando `request.files`: la parte de
        archivo empieza, se spoolea y nunca se cierra, así que nunca aparece
        en `files`."""
        environ, cuerpo = self._cuerpo(b"e" * (2 * UN_MIB))
        estado, _ = self._correr(self._sin_longitud(environ, cuerpo[: UN_MIB]))
        self.assertFalse(estado.startswith("2"), estado)
        self.assertSinRastro()

    def test_un_corte_del_cliente_a_media_subida_no_deja_spool(self):
        environ, cuerpo = self._cuerpo(b"f" * (2 * UN_MIB))
        # Content-Length intacto y menos bytes de los prometidos: Werkzeug lo
        # trata como desconexión, igual que un navegador que cierra la pestaña.
        environ["wsgi.input"] = io.BytesIO(cuerpo[: UN_MIB])
        estado, _ = self._correr(environ)
        self.assertFalse(estado.startswith("2"), estado)
        self.assertSinRastro()

    def test_un_fallo_de_autorizacion_despues_de_parsear_no_deja_spool(self):
        cliente = self.app.test_client()
        respuesta = cliente.post("/protegido",
                                 data={"file": (io.BytesIO(b"g" * (2 * UN_MIB)), "g.bin")},
                                 content_type="multipart/form-data")
        self.assertEqual(401, respuesta.status_code)
        self.assertSinRastro()

    def test_un_cuerpo_por_encima_del_tope_de_la_peticion_no_deja_spool(self):
        @self.app.before_request
        def limitar():
            request.max_content_length = 256 * 1024

        respuesta = self._post(b"h" * (2 * UN_MIB))
        self.assertEqual(413, respuesta.status_code)
        self.assertSinRastro()


class RanurasOcupadasTests(_BaseSpool):
    concurrencia = "1"

    def test_sin_ranura_libre_la_subida_es_503_y_no_507(self):
        """`WorkspaceBusy` hereda de `WorkspaceCapacityExceeded`: si el orden
        de los manejadores se invierte, esto se convierte en un 507 que
        promete al cliente que no hay disco cuando solo hay que esperar."""
        with local_workspace("ingress"):
            respuesta = self._post(b"i" * (2 * UN_MIB))
        self.assertEqual(503, respuesta.status_code)
        self.assertEqual("storage_busy", respuesta.get_json()["code"])
        self.assertSinRastro()


class CableadoEnLaFactoryTests(unittest.TestCase):
    """`application.create_app()` tiene que instalar la clase de request."""

    def _modulo(self):
        import importlib.util

        ruta = Path(__file__).resolve().parents[1] / "app.py"
        spec = importlib.util.spec_from_file_location("albumfp_application_streams", ruta)
        modulo = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(modulo)
        return modulo

    def test_la_factory_instala_la_clase_de_request(self):
        self.assertIs(BudgetedRequest, self._modulo().create_app().request_class)

    def test_instalar_el_spool_no_resuelve_ninguna_raiz_al_arrancar(self):
        """La factory no puede tocar el disco ni validar storage: `create_app()`
        corre en el arranque de Gunicorn, con el entorno que haya, y la liveness
        de la app no depende del almacenamiento."""
        with patch("app.storage.workspace.workspace_root",
                   side_effect=AssertionError("la factory resolvió una raíz")):
            self.assertIsNotNone(self._modulo().create_app())

    def test_el_tope_de_json_sigue_sin_aplicarse_a_un_multipart(self):
        """Instalar el spool no puede reintroducir un tope global: el guard de
        `MAX_JSON_BODY_KB` sigue siendo solo para cuerpos que no son subida."""
        app = self._modulo().create_app()
        self.assertIsNone(app.config.get("MAX_CONTENT_LENGTH"))
        with app.test_request_context("/api/albums", method="POST",
                                      content_type="multipart/form-data"):
            app.preprocess_request()
            self.assertIsNone(request.max_content_length)
        with app.test_request_context("/api/albums", method="POST",
                                      content_type="application/json"):
            app.preprocess_request()
            self.assertEqual(256 * 1024, request.max_content_length)


if __name__ == "__main__":
    unittest.main()
