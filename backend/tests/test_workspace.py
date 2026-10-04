"""Workspace local: temporales gestionados, reservas y leases (spec §11).

Dos propiedades son el motivo entero de este módulo y por eso tienen tests
propios además de los del camino feliz:

* **Un lease es un lock del sistema operativo, no un archivo presente.** Un
  proceso muerto a la fuerza deja su `.lease` en disco pero el kernel suelta
  el lock; si la presencia del archivo bastara, ese residuo quedaría
  protegido del barredor para siempre y su reserva de bytes no volvería
  nunca al presupuesto.
* **La contabilidad es un ledger en disco con lock interproceso, no un
  contador en memoria.** Gunicorn tiene varios workers y los timers son
  procesos distintos: dos de ellos no pueden creerse dueños del mismo
  presupuesto.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.storage.workspace as workspace_mod
from app.storage.contracts import StorageConfigurationError, WorkspaceCapacityExceeded
from app.storage.workspace import (
    WorkspaceBusy,
    local_workspace,
    state_root,
    sweep_workspaces,
    workspace_root,
)

_RAIZ_BACKEND = str(Path(__file__).resolve().parents[1])

# Entra a un workspace, reserva bytes, avisa por stdout y se queda vivo hasta
# que lo maten: es el proceso "otro worker" de los tests de concurrencia.
_HIJO = """
import sys, time
sys.path.insert(0, sys.argv[1])
from app.storage.workspace import local_workspace
with local_workspace(sys.argv[2]) as espacio:
    espacio.reserve_bytes(int(sys.argv[3]))
    sys.stdout.write(espacio.directory.name + "\\n")
    sys.stdout.flush()
    time.sleep(300)
"""


def _entorno(tmp, **extra):
    valores = {
        "MEDIA_WORK_ROOT": str(Path(tmp) / "work"),
        "MEDIA_STATE_ROOT": str(Path(tmp) / "state"),
        "MEDIA_LOCAL_TEMP_MAX_GB": "0",
        "MEDIA_WORK_MAX_CONCURRENT": "4",
        # Explícitos para que el disco real de esta máquina no participe.
        "MEDIA_LOCAL_MIN_FREE_GB": "0",
        "MEDIA_LOCAL_MAX_USAGE_PERCENT": "100",
    }
    valores.update(extra)
    return valores


class _EntornoAislado(unittest.TestCase):
    extra: dict = {}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._patch = patch.dict(os.environ, _entorno(self._tmp.name, **self.extra), clear=False)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def ledger(self) -> dict:
        try:
            return json.loads((workspace_root() / ".ledger.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}


class WorkspaceBasicsTests(_EntornoAislado):
    def test_cede_un_directorio_vacio_que_existe(self):
        with local_workspace("materialize") as ws:
            self.assertTrue(ws.directory.is_dir())
            self.assertEqual([], [p for p in ws.directory.iterdir() if p.name != ".lease"])

    def test_el_directorio_desaparece_al_salir(self):
        with local_workspace("materialize") as ws:
            (ws.directory / "temporal.bin").write_bytes(b"x")
            guardado = ws.directory
        self.assertFalse(guardado.exists())

    def test_el_directorio_desaparece_tambien_si_hay_excepcion(self):
        guardado = None
        with self.assertRaises(RuntimeError):
            with local_workspace("zip") as ws:
                guardado = ws.directory
                (ws.directory / "a.bin").write_bytes(b"x")
                raise RuntimeError("fallo simulado")
        self.assertFalse(guardado.exists())

    def test_el_nombre_es_generado_y_no_se_repite(self):
        with local_workspace("zip") as uno, local_workspace("zip") as otro:
            self.assertNotEqual(uno.directory.name, otro.directory.name)

    def test_un_proposito_desconocido_se_rechaza(self):
        with self.assertRaises(ValueError):
            with local_workspace("lo-que-sea"):
                pass

    def test_el_workspace_no_esta_dentro_de_la_cuarentena_ni_del_state(self):
        raiz, estado = workspace_root(), state_root()
        self.assertNotEqual(raiz, estado)
        self.assertNotIn(estado, raiz.parents)
        self.assertNotIn(raiz, estado.parents)

    def test_raices_solapadas_se_rechazan_en_vez_de_barrer_el_estado(self):
        with patch.dict(os.environ, {"MEDIA_STATE_ROOT": str(workspace_root() / "state")}):
            with self.assertRaises(StorageConfigurationError):
                workspace_root()

    def test_el_workspace_no_puede_vivir_dentro_del_arbol_final_de_media(self):
        media = Path(self._tmp.name) / "media"
        with patch.dict(os.environ, {
            "MEDIA_STORAGE_ROOT": str(media),
            "MEDIA_WORK_ROOT": str(media / "work"),
        }):
            with self.assertRaises(StorageConfigurationError):
                workspace_root()

    @unittest.skipIf(os.name != "posix", "los bits de permiso de Windows no son comparables")
    def test_los_directorios_son_privados(self):
        with local_workspace("materialize") as ws:
            self.assertEqual(0o700, ws.directory.stat().st_mode & 0o777)
            self.assertEqual(0o600, (ws.directory / ".lease").stat().st_mode & 0o777)


class WorkspaceBudgetTests(_EntornoAislado):
    # Presupuesto minúsculo para poder agotarlo en un test: ~1 KiB.
    extra = {"MEDIA_LOCAL_TEMP_MAX_GB": "0.000001", "MEDIA_WORK_MAX_CONCURRENT": "2"}

    def test_reservar_por_debajo_del_presupuesto_funciona(self):
        with local_workspace("materialize") as ws:
            ws.reserve_bytes(500)

    def test_pasarse_del_presupuesto_lanza_workspace_capacity_exceeded(self):
        with local_workspace("materialize") as ws:
            with self.assertRaises(WorkspaceCapacityExceeded):
                ws.reserve_bytes(10 * 1024 * 1024)

    def test_liberar_devuelve_el_presupuesto(self):
        with local_workspace("materialize") as ws:
            ws.reserve_bytes(900)
            with self.assertRaises(WorkspaceCapacityExceeded):
                ws.reserve_bytes(900)
            ws.release_bytes(900)
            ws.reserve_bytes(900)

    def test_salir_del_contexto_libera_todo_lo_reservado(self):
        with local_workspace("materialize") as ws:
            ws.reserve_bytes(900)
        with local_workspace("materialize") as otro:
            otro.reserve_bytes(900)

    def test_dos_workspaces_vivos_comparten_el_mismo_presupuesto(self):
        with local_workspace("materialize") as uno, local_workspace("zip") as otro:
            uno.reserve_bytes(900)
            with self.assertRaises(WorkspaceCapacityExceeded):
                otro.reserve_bytes(900)

    def test_mas_operaciones_concurrentes_que_el_maximo_se_rechazan(self):
        with patch.object(workspace_mod, "_SLOT_WAIT_SECONDS", 0.05):
            with local_workspace("zip"), local_workspace("zip"):
                with self.assertRaises(WorkspaceCapacityExceeded):
                    with local_workspace("zip"):
                        pass

    def test_quedarse_sin_ranura_es_ocupado_recuperable_no_disco_lleno(self):
        with patch.object(workspace_mod, "_SLOT_WAIT_SECONDS", 0.05):
            with local_workspace("zip"), local_workspace("zip"):
                with self.assertRaises(WorkspaceBusy):
                    with local_workspace("zip"):
                        pass

    def test_espera_a_que_se_libere_una_ranura_antes_de_rendirse(self):
        # Spec §11: hasta 2 s de espera por una ranura, y solo después 503.
        soltado = threading.Event()

        def ocupar_un_rato():
            with local_workspace("zip"):
                soltado.wait(0.3)

        with local_workspace("zip"):
            hilo = threading.Thread(target=ocupar_un_rato)
            hilo.start()
            try:
                time.sleep(0.05)
                with local_workspace("materialize") as ws:
                    self.assertTrue(ws.directory.is_dir())
            finally:
                soltado.set()
                hilo.join(30)

    def test_una_entrada_muerta_del_ledger_no_retiene_presupuesto(self):
        raiz = workspace_root()
        raiz.mkdir(parents=True, exist_ok=True)
        (raiz / ".ledger.json").write_text(json.dumps({"zip-difunto": 10**9}), encoding="utf-8")
        with local_workspace("materialize") as ws:
            ws.reserve_bytes(900)

    def test_un_residuo_con_lease_huerfano_tampoco_retiene_presupuesto(self):
        raiz = workspace_root()
        residuo = raiz / "zip-difunto"
        residuo.mkdir(parents=True)
        (residuo / ".lease").write_text("4242", encoding="utf-8")
        (raiz / ".ledger.json").write_text(json.dumps({"zip-difunto": 10**9}), encoding="utf-8")
        with local_workspace("materialize") as ws:
            ws.reserve_bytes(900)

    def test_el_ledger_suma_lo_reservado_por_los_dos_hilos(self):
        # Sin lock interproceso el ledger es un read-modify-write con carrera:
        # dos trabajadores leen el mismo total y un incremento se pierde.
        listos = threading.Barrier(3, timeout=10)
        sueltos = threading.Event()
        errores = []

        def trabajar():
            try:
                with local_workspace("materialize") as ws:
                    for _ in range(25):
                        ws.reserve_bytes(20)
                    listos.wait()
                    sueltos.wait(10)
            except Exception as exc:
                errores.append(exc)
                listos.abort()

        hilos = [threading.Thread(target=trabajar, daemon=True) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        try:
            try:
                listos.wait()
            except threading.BrokenBarrierError:
                self.fail(f"un trabajador no llegó a reservar sus bytes: {errores}")
            self.assertEqual(1000, sum(self.ledger().values()))
        finally:
            sueltos.set()
            for hilo in hilos:
                hilo.join(30)
        self.assertEqual([], errores)


class WorkspacePhysicalSpaceTests(_EntornoAislado):
    extra = {"MEDIA_LOCAL_MIN_FREE_GB": "100000"}

    def test_sin_espacio_fisico_no_se_reserva_aunque_no_haya_presupuesto(self):
        with local_workspace("materialize") as ws:
            with self.assertRaises(WorkspaceCapacityExceeded):
                ws.reserve_bytes(1024)


class WorkspaceSweeperTests(_EntornoAislado):
    def test_borra_un_residuo_viejo_sin_lease(self):
        raiz = workspace_root()
        viejo = raiz / "materialize-abandonado"
        viejo.mkdir(parents=True)
        (viejo / "a.bin").write_bytes(b"x")
        antiguo = time.time() - 48 * 3600
        os.utime(viejo, (antiguo, antiguo))
        self.assertEqual(1, sweep_workspaces(max_age_hours=24))
        self.assertFalse(viejo.exists())

    def test_borra_un_residuo_viejo_con_lease_huerfano(self):
        # Lo que deja un proceso muerto a la fuerza: el archivo sigue ahí, el
        # lock ya no. Si la presencia bastara, esto no se limpiaría jamás.
        raiz = workspace_root()
        viejo = raiz / "zip-matado"
        viejo.mkdir(parents=True)
        (viejo / ".lease").write_text("4242", encoding="utf-8")
        antiguo = time.time() - 48 * 3600
        os.utime(viejo, (antiguo, antiguo))
        self.assertEqual(1, sweep_workspaces(max_age_hours=24))
        self.assertFalse(viejo.exists())

    def test_comprobar_quien_esta_vivo_no_rejuvenece_los_residuos(self):
        # Sondear el lease no puede escribir dentro del residuo: crear ahí un
        # `.lease` refrescaría el mtime del directorio en cada operación y el
        # residuo no cumpliría la edad nunca.
        raiz = workspace_root()
        viejo = raiz / "materialize-abandonado"
        viejo.mkdir(parents=True)
        antiguo = time.time() - 48 * 3600
        os.utime(viejo, (antiguo, antiguo))
        with local_workspace("materialize"):
            pass
        self.assertEqual([], list(viejo.iterdir()))
        self.assertEqual(1, sweep_workspaces(max_age_hours=24))

    def test_conserva_un_residuo_reciente(self):
        raiz = workspace_root()
        reciente = raiz / "materialize-reciente"
        reciente.mkdir(parents=True)
        self.assertEqual(0, sweep_workspaces(max_age_hours=24))
        self.assertTrue(reciente.exists())

    def test_no_borra_un_workspace_con_lease_activo_aunque_envejezca(self):
        with local_workspace("materialize") as ws:
            antiguo = time.time() - 48 * 3600
            os.utime(ws.directory, (antiguo, antiguo))
            self.assertEqual(0, sweep_workspaces(max_age_hours=24))
            self.assertTrue(ws.directory.is_dir())

    def test_el_barredor_tambien_poda_el_ledger(self):
        raiz = workspace_root()
        viejo = raiz / "zip-matado"
        viejo.mkdir(parents=True)
        (viejo / ".lease").write_text("4242", encoding="utf-8")
        (raiz / ".ledger.json").write_text(json.dumps({"zip-matado": 4096}), encoding="utf-8")
        antiguo = time.time() - 48 * 3600
        os.utime(viejo, (antiguo, antiguo))
        sweep_workspaces(max_age_hours=24)
        self.assertNotIn("zip-matado", self.ledger())

    def test_no_toca_el_directorio_de_estado(self):
        registro = state_root() / "storage-operations"
        registro.mkdir(parents=True, exist_ok=True)
        (registro / "op.json").write_text("{}", encoding="utf-8")
        antiguo = time.time() - 72 * 3600
        os.utime(registro, (antiguo, antiguo))
        sweep_workspaces(max_age_hours=24)
        self.assertTrue((registro / "op.json").is_file())


class WorkspaceOtroProcesoTests(_EntornoAislado):
    """Lo que un contador en memoria no puede ver: otro proceso."""

    extra = {"MEDIA_LOCAL_TEMP_MAX_GB": "0.000001", "MEDIA_WORK_MAX_CONCURRENT": "1"}

    def _arrancar_hijo(self, purpose: str, reserva: int) -> tuple[subprocess.Popen, str]:
        proceso = subprocess.Popen(
            [sys.executable, "-c", _HIJO, _RAIZ_BACKEND, purpose, str(reserva)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=dict(os.environ),
        )
        self.addCleanup(self._matar, proceso)
        linea = proceso.stdout.readline().strip()
        if not linea:
            self.fail(f"el proceso hijo no arrancó: {proceso.stdout.read()}")
        return proceso, linea

    def _matar(self, proceso: subprocess.Popen) -> None:
        if proceso.poll() is None:
            proceso.kill()
        proceso.wait(30)
        proceso.stdout.close()

    def _esperar_muerte(self, proceso: subprocess.Popen) -> None:
        proceso.kill()
        proceso.wait(30)

    def test_la_reserva_de_otro_proceso_gasta_el_presupuesto_compartido(self):
        proceso, nombre = self._arrancar_hijo("zip", 900)
        self.assertEqual(900, self.ledger().get(nombre))

        with patch.dict(os.environ, {"MEDIA_WORK_MAX_CONCURRENT": "4"}):
            with local_workspace("materialize") as ws:
                with self.assertRaises(WorkspaceCapacityExceeded):
                    ws.reserve_bytes(900)

            # Muere sin ejecutar ningún `finally`: el residuo queda en disco.
            self._esperar_muerte(proceso)
            self.assertTrue((workspace_root() / nombre / ".lease").is_file())

            # El lock se soltó con el proceso, así que el presupuesto vuelve.
            with local_workspace("materialize") as ws:
                ws.reserve_bytes(900)

    def test_el_lease_de_otro_proceso_ocupa_una_ranura_y_se_recupera_al_morir(self):
        proceso, nombre = self._arrancar_hijo("zip", 0)

        with patch.object(workspace_mod, "_SLOT_WAIT_SECONDS", 0.05):
            with self.assertRaises(WorkspaceBusy):
                with local_workspace("materialize"):
                    pass

        self._esperar_muerte(proceso)
        with local_workspace("materialize") as ws:
            self.assertTrue(ws.directory.is_dir())
        self.assertTrue((workspace_root() / nombre).is_dir())


if __name__ == "__main__":
    unittest.main()


class PreflightTests(_EntornoAislado):
    """L13: la exportación pregunta ANTES de enviar un byte si el mayor
    original cabría en el área de trabajo."""

    extra = {"MEDIA_LOCAL_TEMP_MAX_GB": str(1 / 1024)}  # 1 MiB de presupuesto

    def test_the_preflight_sees_the_budget_and_what_is_already_reserved(self):
        from app.storage.workspace import workspace_can_hold

        self.assertTrue(workspace_can_hold(1024 * 1024))
        self.assertFalse(workspace_can_hold(1024 * 1024 + 1))
        with local_workspace("materialize") as espacio:
            espacio.reserve_bytes(600 * 1024)
            self.assertFalse(workspace_can_hold(600 * 1024))
            self.assertTrue(workspace_can_hold(400 * 1024))
        self.assertTrue(workspace_can_hold(1024 * 1024), "al salir se libera")
