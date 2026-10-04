"""El avatar pasa por el backend y solo borra el anterior tras confirmar."""
import ast
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FUENTE = Path(__file__).resolve().parents[1] / "app" / "api" / "auth.py"
CODIGO = FUENTE.read_text(encoding="utf-8")


def fuente_de(nombre: str) -> str:
    arbol = ast.parse(CODIGO)
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.FunctionDef) and nodo.name == nombre:
            return ast.get_source_segment(CODIGO, nodo)
    raise AssertionError(f"no se encontro {nombre} en auth.py")


class AvatarUploadTests(unittest.TestCase):
    def setUp(self):
        self.fuente = fuente_de("upload_my_avatar")

    def test_registra_la_operacion_antes_de_escribir_la_fila(self):
        self.assertIn("storage_operation_id", self.fuente)
        self.assertIn("mark_committed", self.fuente)
        self.assertIn("mark_rolled_back", self.fuente)

    def test_sigue_sin_generar_vista_previa(self):
        self.assertNotIn("prepare_image_metadata=True", self.fuente)

    def test_el_anterior_se_borra_despues_de_confirmar_el_nuevo(self):
        indice_update = self.fuente.index("SET avatar_path")
        indice_borrado = self.fuente.rindex("remove_stored_file")
        self.assertGreater(indice_borrado, indice_update)

    def test_conserva_el_limite_de_tamano_del_avatar(self):
        self.assertIn("MAX_AVATAR_MB", self.fuente)
        self.assertIn("max_bytes=max_avatar_bytes", self.fuente)

    def test_sigue_exigiendo_que_sea_una_imagen(self):
        self.assertIn('signature[0] != "image"', self.fuente)

    def test_heic_no_se_guarda_como_avatar_sin_un_derivado_web_compatible(self):
        from app.api import auth

        heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic"
        app = Flask(__name__)
        with app.test_request_context(
            "/api/auth/me/avatar",
            method="POST",
            data={"file": (io.BytesIO(heic), "avatar.heic", "image/heic")},
        ):
            with patch.object(auth, "current_user_id", return_value=7), patch.object(
                auth, "save_upload", side_effect=AssertionError("no debe persistir HEIC como avatar")
            ) as save:
                response, status = auth.upload_my_avatar.__wrapped__()

        self.assertEqual(400, status)
        self.assertIn("HEIC", response.get_json()["message"])
        save.assert_not_called()

    def test_un_fallo_al_borrar_el_anterior_no_tumba_la_subida(self):
        """El avatar nuevo ya es el bueno. Si el viejo no se puede borrar
        (origin caido, fichero abierto en Windows), eso es basura para el GC,
        no un error que deba devolverle 500 a quien acaba de cambiar su foto."""
        despues = self.fuente[self.fuente.index("SET avatar_path"):]
        self.assertIn("except StorageError", despues)


class AccountDeletionTests(unittest.TestCase):
    def test_delete_me_recoge_las_tres_fuentes_de_keys(self):
        fuente = fuente_de("delete_me")
        self.assertIn("storage_path", fuente)
        self.assertIn("preview_storage_path", fuente)
        self.assertIn("avatar_path", fuente)

    def test_delete_me_registra_la_intencion_antes_del_delete_de_la_fila(self):
        fuente = fuente_de("delete_me")
        indice_registro = fuente.index("record_pending")
        indice_delete = fuente.index("DELETE FROM users")
        self.assertLess(indice_registro, indice_delete)

    def test_delete_me_informa_si_quedaron_archivos_pendientes(self):
        fuente = fuente_de("delete_me")
        self.assertIn("storage_cleanup_pending", fuente)


class AvatarRouteStaysPublicTests(unittest.TestCase):
    """`/api/users/<id>/avatar` NO lleva sesion a proposito: una etiqueta
    `<img>` nunca pasa por el interceptor de Angular, asi que jamas podria
    mandar la credencial. Es facil "arreglarlo" por error al migrar el avatar,
    y eso romperia todos los avatares de la app ademas del test de superficie."""

    def test_la_ruta_del_avatar_sigue_sin_exigir_sesion(self):
        users = (Path(__file__).resolve().parents[1] / "app" / "api" / "users.py").read_text(encoding="utf-8")
        indice = users.index("def read_user_avatar")
        decoradores = users[:indice].rsplit("@users_bp", 1)[-1]
        self.assertIn("/<int:user_id>/avatar", decoradores)
        self.assertNotIn("session_required", decoradores)


if __name__ == "__main__":
    unittest.main()
