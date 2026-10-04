"""Tipos y jerarquía de errores del protocolo de storage (spec §6)."""
import sys
import unittest
from dataclasses import FrozenInstanceError, fields
from inspect import Parameter, signature
from pathlib import Path
from typing import ContextManager, get_type_hints

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage import contracts
from app.storage.contracts import (
    InvalidStorageKey,
    ObjectConflict,
    ObjectNotFound,
    ObjectPage,
    ObjectStat,
    PutReceipt,
    StorageAuthenticationError,
    StorageBackend,
    StorageCapacity,
    StorageCapacityExceeded,
    StorageConfigurationError,
    StorageError,
    StorageIntegrityError,
    StorageTimeout,
    StorageUnavailable,
    WorkspaceCapacityExceeded,
)


class ErrorHierarchyTests(unittest.TestCase):
    def test_todos_los_errores_derivan_de_storage_error(self):
        for clase in (
            InvalidStorageKey, StorageConfigurationError, ObjectNotFound, ObjectConflict,
            StorageUnavailable, StorageTimeout, StorageAuthenticationError,
            StorageCapacityExceeded, StorageIntegrityError, WorkspaceCapacityExceeded,
        ):
            with self.subTest(clase=clase.__name__):
                self.assertTrue(issubclass(clase, StorageError))

    def test_key_invalida_sigue_siendo_value_error(self):
        self.assertTrue(issubclass(InvalidStorageKey, ValueError))

    def test_un_timeout_es_una_forma_de_indisponibilidad(self):
        self.assertTrue(issubclass(StorageTimeout, StorageUnavailable))

    def test_un_fallo_de_infraestructura_NO_es_value_error(self):
        for clase in (StorageUnavailable, StorageTimeout, StorageAuthenticationError,
                      StorageIntegrityError, StorageCapacityExceeded, ObjectConflict):
            with self.subTest(clase=clase.__name__):
                self.assertFalse(issubclass(clase, ValueError))

    def test_objeto_ausente_no_es_indisponibilidad(self):
        self.assertFalse(issubclass(ObjectNotFound, StorageUnavailable))


class ResultTypeTests(unittest.TestCase):
    def test_esquemas_exactos_de_todos_los_resultados(self):
        expected = {
            ObjectStat: (["key", "size_bytes", "modified_at_ns", "version", "mime_type"],
                         {"key": str, "size_bytes": int, "modified_at_ns": int,
                          "version": str, "mime_type": str}),
            PutReceipt: (["key", "size_bytes", "sha256", "version", "created"],
                         {"key": str, "size_bytes": int, "sha256": str,
                          "version": str, "created": bool}),
            StorageCapacity: (["total_bytes", "used_bytes", "free_bytes", "writable",
                               "mount_ok", "checked_at"],
                              {"total_bytes": int, "used_bytes": int, "free_bytes": int,
                               "writable": bool, "mount_ok": bool, "checked_at": float}),
            ObjectPage: (["snapshot_id", "created_at", "objects", "next_cursor", "complete"],
                         {"snapshot_id": str, "created_at": float,
                          "objects": list[ObjectStat], "next_cursor": str | None,
                          "complete": bool}),
        }
        for tipo, (names, annotations) in expected.items():
            with self.subTest(tipo=tipo.__name__):
                self.assertEqual(names, [field.name for field in fields(tipo)])
                self.assertEqual(annotations, get_type_hints(tipo))
                self.assertTrue(getattr(tipo, "__dataclass_params__").frozen)

    def test_object_stat_es_inmutable_y_no_expone_ruta_fisica(self):
        stat = ObjectStat(key="user_1/album_2/" + "a" * 32 + ".jpg", size_bytes=10,
                          modified_at_ns=1, version="v1", mime_type="image/jpeg")
        with self.assertRaises(FrozenInstanceError):
            stat.size_bytes = 20
        self.assertNotIn("path", stat.__dataclass_fields__)

    def test_put_receipt_lleva_hash_version_y_si_fue_creado(self):
        recibo = PutReceipt(key="user_1/album_2/" + "a" * 32 + ".jpg", size_bytes=10,
                            sha256="0" * 64, version="v1", created=True)
        self.assertTrue(recibo.created)
        with self.assertRaises(FrozenInstanceError):
            recibo.created = False

    def test_capacity_distingue_writable_de_mount_ok(self):
        cap = StorageCapacity(total_bytes=100, used_bytes=10, free_bytes=90,
                              writable=True, mount_ok=False, checked_at=1.0)
        self.assertTrue(cap.writable)
        self.assertFalse(cap.mount_ok)

    def test_object_page_marca_complete_solo_en_la_ultima(self):
        pagina = ObjectPage(snapshot_id="s1", created_at=1.0, objects=[], next_cursor="c1", complete=False)
        self.assertFalse(pagina.complete)
        self.assertIsNone(ObjectPage(snapshot_id="s1", created_at=1.0, objects=[], next_cursor=None,
                                     complete=True).next_cursor)

    def test_ningun_tipo_de_resultado_lleva_hostname_o_url(self):
        prohibidos = {"host", "hostname", "url", "base_url", "endpoint"}
        for tipo in (ObjectStat, PutReceipt, StorageCapacity, ObjectPage):
            with self.subTest(tipo=tipo.__name__):
                self.assertEqual(set(), prohibidos & set(tipo.__dataclass_fields__))


class ProtocolTests(unittest.TestCase):
    def test_firma_exacta_de_los_siete_metodos(self):
        expected = {
            "put_from_path": [
                ("self", Parameter.POSITIONAL_OR_KEYWORD, None, Parameter.empty),
                ("relative_key", Parameter.POSITIONAL_OR_KEYWORD, str, Parameter.empty),
                ("local_source_path", Parameter.POSITIONAL_OR_KEYWORD, Path, Parameter.empty),
            ],
            "materialize": [
                ("self", Parameter.POSITIONAL_OR_KEYWORD, None, Parameter.empty),
                ("relative_key", Parameter.POSITIONAL_OR_KEYWORD, str, Parameter.empty),
            ],
            "exists": [("self", Parameter.POSITIONAL_OR_KEYWORD, None, Parameter.empty),
                       ("relative_key", Parameter.POSITIONAL_OR_KEYWORD, str, Parameter.empty)],
            "stat": [("self", Parameter.POSITIONAL_OR_KEYWORD, None, Parameter.empty),
                     ("relative_key", Parameter.POSITIONAL_OR_KEYWORD, str, Parameter.empty)],
            "delete": [("self", Parameter.POSITIONAL_OR_KEYWORD, None, Parameter.empty),
                       ("relative_key", Parameter.POSITIONAL_OR_KEYWORD, str, Parameter.empty),
                       ("expected_version", Parameter.KEYWORD_ONLY, str | None, None)],
            "capacity": [("self", Parameter.POSITIONAL_OR_KEYWORD, None, Parameter.empty)],
            "list_objects": [("self", Parameter.POSITIONAL_OR_KEYWORD, None, Parameter.empty),
                             ("cursor", Parameter.KEYWORD_ONLY, str | None, None),
                             ("limit", Parameter.KEYWORD_ONLY, int, 500)],
        }
        returns = {
            "put_from_path": PutReceipt, "materialize": ContextManager[Path],
            "exists": bool, "stat": ObjectStat, "delete": bool,
            "capacity": StorageCapacity, "list_objects": ObjectPage,
        }
        for name, params in expected.items():
            with self.subTest(method=name):
                method = getattr(StorageBackend, name)
                actual = signature(method).parameters
                self.assertEqual([item[0] for item in params], list(actual))
                for parameter, (_, kind, annotation, default) in zip(actual.values(), params):
                    self.assertEqual(kind, parameter.kind)
                    self.assertEqual(default, parameter.default)
                    if annotation is not None:
                        self.assertEqual(annotation, get_type_hints(method)[parameter.name])
                self.assertEqual(returns[name], get_type_hints(method)["return"])

    def test_protocolo_es_runtime_checkable_y_reexporta_la_misma_excepcion(self):
        class Complete:
            def put_from_path(self, relative_key, local_source_path): pass
            def materialize(self, relative_key): pass
            def exists(self, relative_key): pass
            def stat(self, relative_key): pass
            def delete(self, relative_key, *, expected_version=None): pass
            def capacity(self): pass
            def list_objects(self, *, cursor=None, limit=500): pass

        class Incomplete:
            def exists(self, relative_key): pass

        self.assertTrue(isinstance(Complete(), StorageBackend))
        self.assertFalse(isinstance(Incomplete(), StorageBackend))
        from app.storage import object_keys
        self.assertIs(InvalidStorageKey, contracts.InvalidStorageKey)
        self.assertIs(object_keys.InvalidStorageKey, contracts.InvalidStorageKey)

    def test_el_protocolo_declara_exactamente_los_siete_metodos(self):
        esperados = {"put_from_path", "materialize", "exists", "stat", "delete", "capacity", "list_objects"}
        publicos = {n for n in dir(StorageBackend) if not n.startswith("_")}
        self.assertEqual(esperados, publicos)

    def test_el_modulo_no_importa_flask_ni_sqlalchemy(self):
        fuente = Path(contracts.__file__).read_text(encoding="utf-8")
        self.assertNotIn("flask", fuente.lower())
        self.assertNotIn("sqlalchemy", fuente.lower())


if __name__ == "__main__":
    unittest.main()
