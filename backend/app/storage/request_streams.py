"""El spool de Werkzeug para un multipart grande, dentro del workspace.

Antes de esto, un archivo grande se spooleaba donde dijera `tempfile`
(`SpooledTemporaryFile`, que vuelca a `/tmp` por encima de 500 KiB), fuera del
árbol que la app controla y sin contar contra ningún presupuesto. Aquí cae en
`MEDIA_WORK_ROOT`, se contabiliza y se borra al cerrar la petición.

Tres decisiones son el módulo entero:

* **Los bytes se reservan mientras entran, no cuando Werkzeug termina.** Con
  `proxy_request_buffering off` y HTTP/1.1 al upstream, Nginx manda el cuerpo
  troceado y `Content-Length` no existe: no hay nada que mirar antes de
  escribir. Un `Content-Length` presente sí permite rechazo temprano —una sola
  reserva antes de abrir el archivo—, pero nunca sustituye a medir los bytes
  reales, así que cada `write()` sigue pasando por el presupuesto.
* **El spool es un `local_workspace("ingress")`, no un directorio suelto.** Un
  directorio compartido sin lease sería basura envejecida para
  `sweep_workspaces()`, que lo borraría por debajo de una subida en curso de
  otro proceso. Con lease, el barredor no lo toca mientras viva la petición.
* **La limpieza recorre los streams que este módulo entregó, no
  `request.files`.** Un multipart truncado spoolea la parte de archivo y nunca
  llega a cerrarla, así que esa parte NUNCA aparece en `request.files`: limpiar
  mirando ahí dejaría el archivo en disco justo en el caso que más importa.
"""
from __future__ import annotations

import io
import os
import uuid
from contextlib import ExitStack

from flask import Flask, Request, request

from ..utils.responses import fail
from .contracts import WorkspaceCapacityExceeded
from .workspace import WorkspaceBusy, local_workspace

# Por debajo de esto Werkzeug trabaja en memoria y no hace falta tocar disco.
_SPOOL_THRESHOLD_BYTES = 512 * 1024
# Tramo de reserva cuando no hay `Content-Length`: cada `reserve_bytes()` toma
# el lock del ledger y lo reescribe, así que reservar por bloque de parser
# (64 KiB) serían miles de ciclos de disco por subida grande.
_RESERVE_BLOCK_BYTES = 8 * 1024 * 1024


class _SpooledPart:
    """Archivo del spool que reserva presupuesto antes de escribir un bloque.

    Delega todo lo demás (`read`, `seek`, `tell`, `flush`...) en el archivo
    real: `quarantined_upload()` y `upload_size()` lo usan como un archivo
    normal y no tienen por qué saber que hay un presupuesto detrás.
    """

    # Atributo de clase: si `open()` fallara, `__getattr__` buscaría `_handle`
    # y se llamaría a sí mismo para siempre.
    _handle = None

    def __init__(self, reservar, path):
        self._reservar = reservar
        self.name = str(path)
        self._handle = open(path, "w+b")
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover - depende del sistema de archivos
            pass

    def write(self, data):
        self._reservar(len(data))
        return self._handle.write(data)

    def close(self):
        if self._handle is not None:
            self._handle.close()

    def __getattr__(self, name):
        return getattr(self._handle, name)


class BudgetedRequest(Request):
    # Valores por defecto como atributos de clase: una petición que nunca
    # spoolea nada no paga ni una asignación.
    _spool_stack: ExitStack | None = None
    _spool_room = None
    _spool_free = 0
    _spool_files: tuple = ()

    def _get_file_stream(self, total_content_length, content_type, filename=None,
                         content_length=None):
        if total_content_length is not None and total_content_length <= _SPOOL_THRESHOLD_BYTES:
            # `BytesIO` y no `super()`: el spool por defecto de Werkzeug vuelca
            # a /tmp por encima de 500 KiB pase lo que pase, y ese es
            # justamente el spool sin control que esta clase viene a cerrar.
            return io.BytesIO()

        espacio = self._spool_space(total_content_length)
        flujo = _SpooledPart(self._reserve_spool,
                             espacio.directory / f"{uuid.uuid4().hex}.part")
        # delete-on-close no vale: Werkzeug rebobina y relee el stream, y en
        # Windows un archivo con esa marca no se puede reabrir. Se borra al
        # deshacer el workspace, en el teardown de la petición.
        self._spool_files = self._spool_files + (flujo,)
        return flujo

    def _spool_space(self, total_content_length):
        if self._spool_room is None:
            # ponytail: una subida grande ocupa una de las
            # `MEDIA_WORK_MAX_CONCURRENT` ranuras durante TODA la petición, así
            # que con el valor por defecto (4) son 4 subidas grandes a la vez
            # como mucho, y lo que la petición pida después —una preview, por
            # ejemplo— compite contra ellas. Es lo que pide la spec (el spool
            # cuenta como operación costosa); si algún día estorba, la salida
            # es subir ese número, no soltar el lease a mitad de subida.
            stack = ExitStack()
            # El stack se guarda ANTES de reservar: si la reserva no cabe, el
            # teardown tiene que encontrar el workspace para deshacerlo.
            self._spool_stack = stack
            self._spool_room = stack.enter_context(local_workspace("ingress"))
            declarado = int(total_content_length or 0)
            if declarado > 0:
                # Rechazo temprano: un cuerpo que no cabe se rechaza sin abrir
                # el archivo ni escribir un solo byte.
                self._spool_room.reserve_bytes(declarado)
                self._spool_free = declarado
        return self._spool_room

    def _reserve_spool(self, n):
        if n <= 0:
            return
        if self._spool_free < n:
            bloque = max(_RESERVE_BLOCK_BYTES, n - self._spool_free)
            self._spool_room.reserve_bytes(bloque)
            self._spool_free += bloque
        self._spool_free -= n

    def _close_spools(self):
        """Cierra y borra todo lo spooleado. Idempotente y sin excepciones:
        corre en el teardown, donde ya puede haber un error en curso."""
        for flujo in self._spool_files:
            # Un cierre que falla no puede impedir que se deshaga el workspace
            # de abajo: eso dejaría justo el residuo que este método evita.
            try:
                flujo.close()
            except Exception:  # noqa: BLE001
                pass
        self._spool_files = ()
        stack, self._spool_stack = self._spool_stack, None
        self._spool_room = None
        self._spool_free = 0
        if stack is not None:
            # Cerrar antes de deshacer: en Windows no se borra un archivo
            # abierto, y `_dispose()` se traga ese error en silencio.
            stack.close()


def install_budgeted_requests(app: Flask) -> None:
    app.request_class = BudgetedRequest

    @app.teardown_request
    def _soltar_spool(_exc=None):
        cerrar = getattr(request, "_close_spools", None)
        if cerrar is not None:
            cerrar()

    # `WorkspaceBusy` hereda de `WorkspaceCapacityExceeded`, y Flask resuelve
    # por MRO: el manejador de la subclase gana. Sin él, quedarse sin ranura
    # —esperar unos segundos— se le contaría al cliente como quedarse sin
    # disco. Estos dos errores nacen dentro del parseo, fuera de cualquier
    # `try` de un endpoint, así que solo un manejador puede traducirlos.
    @app.errorhandler(WorkspaceBusy)
    def _almacenamiento_ocupado(_e):
        return fail("El almacenamiento está ocupado; reinténtalo en unos segundos",
                    status=503, code="storage_busy")

    @app.errorhandler(WorkspaceCapacityExceeded)
    def _sin_espacio_temporal(_e):
        return fail("No hay espacio temporal para procesar la subida",
                    status=507, code="workspace_capacity_exceeded")
