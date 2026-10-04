import io
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.rules import (
    ALBUM_CAPABILITIES,
    SHARE_PASSWORD_MAX_LENGTH,
    SHARE_PASSWORD_MIN_LENGTH,
    capabilities_for,
    normalize_capabilities,
    normalize_share_request,
    permission_allows,
    validate_media_update_fields,
    validate_media_upload_fields,
    validate_notification_preferences_update,
    validate_registration_fields,
    validate_share_password,
    validate_share_permission_update,
    validate_tag_ids,
)
import app.storage.media_storage as media_storage
import app.storage.quarantine as quarantine
from app.storage.media_storage import canonical_mime_type, detect_media_signature


class PermissionRulesTests(unittest.TestCase):
    def test_owner_has_all_album_capabilities(self):
        for required in ("read", "write", "owner"):
            self.assertTrue(permission_allows("owner", required))

    def test_write_does_not_gain_owner_capabilities(self):
        self.assertTrue(permission_allows("write", "read"))
        self.assertTrue(permission_allows("write", "write"))
        self.assertFalse(permission_allows("write", "owner"))

    def test_read_is_read_only(self):
        self.assertTrue(permission_allows("read", "read"))
        self.assertFalse(permission_allows("read", "write"))
        self.assertFalse(permission_allows("read", "owner"))

    def test_unknown_role_or_requirement_is_never_allowed(self):
        self.assertFalse(permission_allows("admin", "read"))
        self.assertFalse(permission_allows("owner", "admin"))
        self.assertFalse(permission_allows("", "read"))


class CapabilityRulesTests(unittest.TestCase):
    def test_el_dueno_las_tiene_todas_sin_depender_de_ninguna_fila(self):
        self.assertEqual(capabilities_for("owner", None), set(ALBUM_CAPABILITIES))
        self.assertEqual(capabilities_for("owner", []), set(ALBUM_CAPABILITIES))

    def test_un_colaborador_tiene_exactamente_lo_concedido(self):
        self.assertEqual(capabilities_for("write", ["upload"]), {"upload"})
        self.assertEqual(capabilities_for("write", []), set())

    def test_solo_lectura_nunca_tiene_capacidades(self):
        # Aunque la fila trajera capacidades (dato corrupto o una migracion a
        # medias), 'read' no puede conceder escritura.
        self.assertEqual(capabilities_for("read", ["upload", "edit_album"]), set())
        self.assertEqual(capabilities_for("read", []), set())

    def test_una_capacidad_desconocida_se_descarta_al_leer(self):
        self.assertEqual(capabilities_for("write", ["upload", "hackear"]), {"upload"})

    def test_normalize_ordena_y_quita_duplicados(self):
        self.assertEqual(normalize_capabilities(["organize", "upload", "upload"]), ["upload", "organize"])

    def test_normalize_rechaza_lo_desconocido_en_vez_de_ignorarlo(self):
        with self.assertRaisesRegex(ValueError, "desconocida"):
            normalize_capabilities(["upload", "borrar_todo"])
        with self.assertRaisesRegex(ValueError, "lista"):
            normalize_capabilities("upload")

    def test_normalize_acepta_vacio(self):
        self.assertEqual(normalize_capabilities(None), [])
        self.assertEqual(normalize_capabilities([]), [])


class RegistrationRulesTests(unittest.TestCase):
    def test_normalizes_username_and_full_name(self):
        data = validate_registration_fields("  ari_rodas  ", "  Ariana Rodas  ")
        self.assertEqual(data["username"], "ari_rodas")
        self.assertEqual(data["full_name"], "Ariana Rodas")

    def test_username_is_required(self):
        with self.assertRaisesRegex(ValueError, "username es obligatorio"):
            validate_registration_fields("   ", "Ariana Rodas")

    def test_rejects_oversized_identity_fields(self):
        with self.assertRaisesRegex(ValueError, "50"):
            validate_registration_fields("a" * 51, "Ariana Rodas")
        with self.assertRaisesRegex(ValueError, "120"):
            validate_registration_fields("ariana", "n" * 121)

    def test_full_name_is_optional_and_normalized_to_none(self):
        self.assertIsNone(validate_registration_fields("ariana", "")["full_name"])
        self.assertIsNone(validate_registration_fields("ariana")["full_name"])


class ShareRulesTests(unittest.TestCase):
    def test_requires_exactly_one_share_target(self):
        with self.assertRaisesRegex(ValueError, "exactamente un tipo"):
            normalize_share_request({"permission": "read"})
        with self.assertRaisesRegex(ValueError, "exactamente un tipo"):
            normalize_share_request({
                "permission": "read",
                "shared_with_user_id": 2,
                "create_link": True,
            })

    def test_supports_direct_user_target(self):
        result = normalize_share_request({
            "permission": "write",
            "capabilities": ["upload"],
            "shared_with_user_id": 9,
            "expires_at": "2026-09-01T10:30:00",
        })
        self.assertEqual(result["permission"], "write")
        self.assertEqual(result["capabilities"], ["upload"])
        self.assertEqual(result["target_type"], "user")
        self.assertEqual(result["shared_with_user_id"], 9)
        self.assertEqual(result["share_type"], "account")

    def test_rejects_legacy_email_target(self):
        with self.assertRaisesRegex(ValueError, "correo"):
            normalize_share_request({"permission": "read", "shared_with_email": "person@example.com"})

    def test_rejects_invalid_expiry(self):
        with self.assertRaisesRegex(ValueError, "expires_at"):
            normalize_share_request({"create_link": True, "expires_at": "mañana"})

    def test_supports_account_and_public_link_types(self):
        account = normalize_share_request({"create_link": True, "permission": "write", "capabilities": ["upload"], "share_type": "account"})
        self.assertEqual(account["share_type"], "account")
        public = normalize_share_request({"create_link": True, "permission": "read", "share_type": "public_link"})
        self.assertEqual(public["share_type"], "public_link")

    def test_public_link_cannot_be_collaborator(self):
        with self.assertRaisesRegex(ValueError, "público"):
            normalize_share_request({"create_link": True, "permission": "write", "capabilities": ["upload"], "share_type": "public_link"})

    def test_rejects_a_permission_outside_the_two_profiles(self):
        for invalido in ("admin", "upload", "owner"):
            with self.subTest(permission=invalido):
                with self.assertRaisesRegex(ValueError, "permission"):
                    normalize_share_request({"permission": invalido, "shared_with_user_id": 9})

    def test_un_colaborador_sin_ninguna_capacidad_se_rechaza(self):
        # Seria indistinguible de solo lectura pero pintado como colaborador.
        with self.assertRaisesRegex(ValueError, "al menos una"):
            normalize_share_request({"permission": "write", "capabilities": [], "shared_with_user_id": 9})

    def test_solo_lectura_no_puede_llevar_capacidades(self):
        with self.assertRaisesRegex(ValueError, "solo lectura"):
            normalize_share_request({"permission": "read", "capabilities": ["upload"], "shared_with_user_id": 9})

    def test_un_enlace_publico_sin_extras_usa_los_defaults_mas_restrictivos(self):
        public = normalize_share_request({"create_link": True, "permission": "read", "share_type": "public_link"})
        self.assertIsNone(public["password"])
        self.assertFalse(public["allow_original_download"])
        self.assertTrue(public["show_metadata"])

    def test_un_enlace_publico_puede_traer_password_y_los_dos_interruptores(self):
        public = normalize_share_request({
            "create_link": True, "permission": "read", "share_type": "public_link",
            "password": "unaClaveDecente", "allow_original_download": True, "show_metadata": False,
        })
        self.assertEqual(public["password"], "unaClaveDecente")
        self.assertTrue(public["allow_original_download"])
        self.assertFalse(public["show_metadata"])

    def test_una_invitacion_de_cuenta_no_puede_llevar_los_extras_de_enlace_publico(self):
        with self.assertRaisesRegex(ValueError, "contraseña"):
            normalize_share_request({"create_link": True, "permission": "write", "capabilities": ["upload"],
                                      "share_type": "account", "password": "unaClaveDecente"})
        with self.assertRaisesRegex(ValueError, "original"):
            normalize_share_request({"create_link": True, "permission": "write", "capabilities": ["upload"],
                                      "share_type": "account", "allow_original_download": True})
        with self.assertRaisesRegex(ValueError, "metadatos"):
            normalize_share_request({"create_link": True, "permission": "write", "capabilities": ["upload"],
                                      "share_type": "account", "show_metadata": False})
        with self.assertRaisesRegex(ValueError, "contraseña"):
            normalize_share_request({"permission": "write", "capabilities": ["upload"],
                                      "shared_with_user_id": 9, "password": "unaClaveDecente"})

    def test_allow_original_download_y_show_metadata_deben_ser_booleanos(self):
        with self.assertRaisesRegex(ValueError, "allow_original_download"):
            normalize_share_request({"create_link": True, "permission": "read", "share_type": "public_link",
                                      "allow_original_download": "true"})
        with self.assertRaisesRegex(ValueError, "show_metadata"):
            normalize_share_request({"create_link": True, "permission": "read", "share_type": "public_link",
                                      "show_metadata": 1})


class SharePasswordRulesTests(unittest.TestCase):
    def test_vacio_o_ausente_es_ninguna_contrasena(self):
        self.assertIsNone(validate_share_password(None))
        self.assertIsNone(validate_share_password(""))

    def test_demasiado_corta_o_demasiado_larga_se_rechaza(self):
        with self.assertRaisesRegex(ValueError, "entre"):
            validate_share_password("a" * (SHARE_PASSWORD_MIN_LENGTH - 1))
        with self.assertRaisesRegex(ValueError, "entre"):
            validate_share_password("a" * (SHARE_PASSWORD_MAX_LENGTH + 1))

    def test_en_los_limites_exactos_se_acepta(self):
        self.assertEqual(len(validate_share_password("a" * SHARE_PASSWORD_MIN_LENGTH)), SHARE_PASSWORD_MIN_LENGTH)
        self.assertEqual(len(validate_share_password("a" * SHARE_PASSWORD_MAX_LENGTH)), SHARE_PASSWORD_MAX_LENGTH)

    def test_no_string_se_rechaza(self):
        with self.assertRaisesRegex(ValueError, "texto"):
            validate_share_password(12345678)


class SharePermissionUpdateRulesTests(unittest.TestCase):
    def test_accepts_both_profiles(self):
        self.assertEqual(validate_share_permission_update({"permission": "read"}), ("read", [], {}))
        self.assertEqual(
            validate_share_permission_update({"permission": "write", "capabilities": ["organize", "upload"]}),
            ("write", ["upload", "organize"], {}),
        )

    def test_rejects_missing_or_unknown_permission(self):
        with self.assertRaisesRegex(ValueError, "permission"):
            validate_share_permission_update({})
        with self.assertRaisesRegex(ValueError, "permission"):
            validate_share_permission_update({"permission": "owner"})

    def test_rejects_a_collaborator_with_no_capabilities(self):
        with self.assertRaisesRegex(ValueError, "al menos una"):
            validate_share_permission_update({"permission": "write", "capabilities": []})

    def test_las_claves_de_enlace_publico_son_un_patch_parcial(self):
        _, _, updates = validate_share_permission_update({"permission": "read"})
        self.assertEqual(updates, {})

        _, _, updates = validate_share_permission_update({"permission": "read", "password": "unaClaveDecente"})
        self.assertEqual(updates, {"password": "unaClaveDecente"})

        _, _, updates = validate_share_permission_update({"permission": "read", "password": ""})
        self.assertEqual(updates, {"password": None})

        _, _, updates = validate_share_permission_update({
            "permission": "read", "allow_original_download": True, "show_metadata": False,
        })
        self.assertEqual(updates, {"allow_original_download": True, "show_metadata": False})

    def test_los_valores_de_enlace_publico_deben_tener_el_tipo_correcto(self):
        with self.assertRaisesRegex(ValueError, "entre"):
            validate_share_permission_update({"permission": "read", "password": "ab"})
        with self.assertRaisesRegex(ValueError, "allow_original_download"):
            validate_share_permission_update({"permission": "read", "allow_original_download": "yes"})
        with self.assertRaisesRegex(ValueError, "show_metadata"):
            validate_share_permission_update({"permission": "read", "show_metadata": "no"})


class NotificationPreferencesRulesTests(unittest.TestCase):
    def test_accepts_partial_boolean_updates(self):
        self.assertEqual(
            validate_notification_preferences_update({
                "notify_album_invites": False, "notify_shared_album_uploads": True,
            }),
            {"notify_album_invites": False, "notify_shared_album_uploads": True},
        )

    def test_empty_payload_is_a_no_op_not_an_error(self):
        self.assertEqual(validate_notification_preferences_update({}), {})

    def test_rejects_unknown_field(self):
        with self.assertRaisesRegex(ValueError, "Campo desconocido"):
            validate_notification_preferences_update({"is_admin": True})

    def test_rejects_non_boolean_values(self):
        with self.assertRaisesRegex(ValueError, "booleano"):
            validate_notification_preferences_update({"notify_album_invites": "true"})
        with self.assertRaisesRegex(ValueError, "booleano"):
            validate_notification_preferences_update({"notify_share_claimed": 1})

    def test_the_retired_push_flag_is_now_an_unknown_field(self):
        """L12: las notificaciones son in-app; no hay interruptor de push."""
        with self.assertRaisesRegex(ValueError, "Campo desconocido"):
            validate_notification_preferences_update({"web_push_enabled": True})

    def test_rejects_non_dict_payload(self):
        with self.assertRaises(ValueError):
            validate_notification_preferences_update(["not", "a", "dict"])


class MediaRulesTests(unittest.TestCase):
    def test_validates_and_normalizes_upload_fields(self):
        data = validate_media_upload_fields({
            "title": "  Atardecer en Quito  ",
            "caption": "  Viaje  ",
            "is_favorite": "true",
            "taken_at": "2026-08-01T12:00:00",
            "resolution": " 1920x1080 ",
            "duration": "12",
            "tag_ids": "3,1,3",
        }, file_type="video")
        self.assertEqual(data["title"], "Atardecer en Quito")
        self.assertEqual(data["caption"], "Viaje")
        self.assertTrue(data["is_favorite"])
        self.assertEqual(data["resolution"], "1920x1080")
        self.assertEqual(data["duration"], 12)
        self.assertEqual(data["tag_ids"], [3, 1])

    def test_rejects_duration_for_images_and_bad_favorite(self):
        with self.assertRaisesRegex(ValueError, "duration"):
            validate_media_upload_fields({"duration": "12"}, file_type="image")
        with self.assertRaisesRegex(ValueError, "is_favorite"):
            validate_media_upload_fields({"is_favorite": "maybe"}, file_type="image")

    def test_force_duplicate_is_strict_and_defaults_to_false(self):
        self.assertFalse(validate_media_upload_fields({}, file_type="image")["force_duplicate"])
        self.assertTrue(validate_media_upload_fields(
            {"force_duplicate": "true"}, file_type="image")["force_duplicate"])
        self.assertFalse(validate_media_upload_fields(
            {"force_duplicate": "0"}, file_type="image")["force_duplicate"])
        with self.assertRaisesRegex(ValueError, "force_duplicate"):
            validate_media_upload_fields({"force_duplicate": "yes"}, file_type="image")

    def test_pin_manual_se_acepta_como_par_de_coordenadas(self):
        data = validate_media_upload_fields({"latitude": "-2.18", "longitude": "-79.88"}, file_type="image")
        self.assertEqual(data["latitude"], -2.18)
        self.assertEqual(data["longitude"], -79.88)

    def test_pin_manual_es_completamente_opcional(self):
        data = validate_media_upload_fields({}, file_type="image")
        self.assertIsNone(data["latitude"])
        self.assertIsNone(data["longitude"])

    def test_pin_manual_exige_las_dos_coordenadas_juntas(self):
        with self.assertRaisesRegex(ValueError, "juntas"):
            validate_media_upload_fields({"latitude": "-2.18"}, file_type="image")
        with self.assertRaisesRegex(ValueError, "juntas"):
            validate_media_upload_fields({"longitude": "-79.88"}, file_type="image")

    def test_pin_manual_fuera_de_rango_se_rechaza(self):
        with self.assertRaisesRegex(ValueError, "latitude"):
            validate_media_upload_fields({"latitude": "200", "longitude": "0"}, file_type="image")
        with self.assertRaisesRegex(ValueError, "longitude"):
            validate_media_upload_fields({"latitude": "0", "longitude": "200"}, file_type="image")

    def test_tag_ids_must_be_unique_positive_integers(self):
        self.assertEqual(validate_tag_ids([4, 2, 4]), [4, 2])
        with self.assertRaisesRegex(ValueError, "enteros positivos"):
            validate_tag_ids([1, 0, "2"])

    def test_detects_supported_file_signatures(self):
        self.assertEqual(detect_media_signature(b"\xff\xd8\xff\xe0", "photo.jpg", "image/jpeg"), ("image", "jpg"))
        self.assertEqual(detect_media_signature(b"\x89PNG\r\n\x1a\n", "photo.png", "image/png"), ("image", "png"))
        self.assertEqual(detect_media_signature(b"\x00\x00\x00\x18ftypisom", "clip.mp4", "video/mp4"), ("video", "mp4"))
        with self.assertRaisesRegex(ValueError, "no compatible"):
            detect_media_signature(b"%PDF", "doc.pdf", "application/pdf")

    def test_detects_heic_and_heif_iso_bmff_signatures(self):
        heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic"
        heif = b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00mif1heix"

        self.assertEqual(
            detect_media_signature(heic, "iphone.heic", "image/heic"),
            ("image", "heic"),
        )
        self.assertEqual(
            detect_media_signature(heif, "camera.heif", "image/heif"),
            ("image", "heif"),
        )
        self.assertEqual("image/heic", canonical_mime_type("heic"))
        self.assertEqual("image/heif", canonical_mime_type("heif"))

    def test_rejects_heif_when_extension_or_mime_disagrees(self):
        heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic"

        for filename, mime in (
            ("iphone.jpg", "image/heic"),
            ("iphone.heic", "image/jpeg"),
            ("iphone.heic", "image/heif"),
            ("iphone.heif", "image/heic"),
            ("iphone.heic", "image/heic-sequence"),
            ("iphone.heif", "image/heif-sequence"),
        ):
            with self.subTest(filename=filename, mime=mime):
                with self.assertRaisesRegex(ValueError, "no compatible"):
                    detect_media_signature(heic, filename, mime)

    def test_rejects_hevc_sequence_brands_even_with_a_still_or_empty_mime(self):
        for brand in (b"hevc", b"hevx"):
            header = b"\x00\x00\x00\x18ftyp" + brand + b"\x00\x00\x00\x00mif1" + brand
            for filename, mime in (
                ("sequence.heic", "image/heic"),
                ("sequence.heif", "image/heif"),
                ("sequence.heic", ""),
            ):
                with self.subTest(brand=brand, filename=filename, mime=mime):
                    with self.assertRaisesRegex(ValueError, "no compatible"):
                        detect_media_signature(header, filename, mime)

    def test_rejects_mixed_still_and_sequence_heif_brands(self):
        mixed_headers = (
            b"\x00\x00\x00\x1cftyphevc\x00\x00\x00\x00mif1heic",
            b"\x00\x00\x00\x1cftypheic\x00\x00\x00\x00mif1hevx",
        )
        for header in mixed_headers:
            with self.subTest(header=header):
                with self.assertRaisesRegex(ValueError, "no compatible"):
                    detect_media_signature(header, "mixed.heic", "image/heic")


class MediaUpdateRulesTests(unittest.TestCase):
    def test_edicion_parcial_solo_devuelve_lo_enviado(self):
        data = validate_media_update_fields({"title": "  Nuevo título  "})
        self.assertEqual(data, {"title": "Nuevo título"})

    def test_payload_vacio_no_actualiza_nada(self):
        self.assertEqual(validate_media_update_fields({}), {})

    def test_favorito_y_tags_no_pasan_por_aqui(self):
        data = validate_media_update_fields({"is_favorite": True, "tag_ids": [1, 2]})
        self.assertEqual(data, {})

    def test_caption_se_puede_vaciar_explicitamente(self):
        data = validate_media_update_fields({"caption": ""})
        self.assertEqual(data, {"caption": None})

    def test_title_respeta_el_mismo_limite_que_al_subir(self):
        with self.assertRaisesRegex(ValueError, "120"):
            validate_media_update_fields({"title": "a" * 121})

    def test_coordenadas_exigen_las_dos_juntas_igual_que_al_subir(self):
        with self.assertRaisesRegex(ValueError, "juntas"):
            validate_media_update_fields({"latitude": "-2.18"})

    def test_corrige_coordenadas_existentes(self):
        data = validate_media_update_fields({"latitude": "-2.17", "longitude": "-79.92"})
        self.assertEqual(data, {"latitude": -2.17, "longitude": -79.92})

    def test_vaciar_la_fecha_pide_volver_al_exif_no_borrarla(self):
        data = validate_media_update_fields({"taken_at": ""})
        self.assertEqual(data, {"taken_at": None, "reset_taken_at": True})

    def test_vaciar_las_coordenadas_pide_volver_al_exif(self):
        data = validate_media_update_fields({"latitude": None, "longitude": None})
        self.assertEqual(data, {"reset_location": True})

    def test_clear_location_explicito_pide_lo_mismo(self):
        self.assertEqual(validate_media_update_fields({"clear_location": True}), {"reset_location": True})

    def test_clear_location_gana_sobre_unas_coordenadas_en_el_mismo_payload(self):
        # Mandar las dos cosas es contradictorio; borrar es lo mas seguro.
        data = validate_media_update_fields({"clear_location": True, "latitude": 1.0, "longitude": 2.0})
        self.assertEqual(data, {"reset_location": True})

    def test_una_fecha_a_medias_se_rechaza(self):
        for incompleta in ("2026", "2026-08", "08-2026", "26-08-2026"):
            with self.subTest(valor=incompleta):
                with self.assertRaisesRegex(ValueError, "día, mes y año"):
                    validate_media_update_fields({"taken_at": incompleta})

    def test_la_hora_es_opcional_pero_el_dia_no(self):
        self.assertEqual(validate_media_update_fields({"taken_at": "2026-08-23"})["taken_at"], "2026-08-23")
        self.assertEqual(validate_media_update_fields({"taken_at": "2026-08-23T15:30"})["taken_at"], "2026-08-23T15:30")


class _FakeUpload:
    def __init__(self, data: bytes, filename: str, mimetype: str):
        self.stream = io.BytesIO(data)
        self.filename = filename
        self.mimetype = mimetype

    def save(self, destination):
        self.stream.seek(0)
        Path(destination).write_bytes(self.stream.read())


class StorageRulesTests(unittest.TestCase):
    def test_saves_file_under_owner_album_and_removes_it(self):
        original_root = media_storage._STORAGE_ROOT
        original_quarantine = quarantine._QUARANTINE_ROOT
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as tmp_q:
            media_storage._STORAGE_ROOT = Path(tmp)
            quarantine._QUARANTINE_ROOT = Path(tmp_q)
            try:
                # Una imagen REAL y decodificable, no solo una cabecera JPEG
                # valida: desde S06 el pipeline la decodifica de verdad con
                # Pillow, no solo le mira la firma de bytes.
                buffer = io.BytesIO()
                Image.new("RGB", (10, 10), "red").save(buffer, format="JPEG")
                upload = _FakeUpload(buffer.getvalue(), "Mi foto.jpg", "image/jpeg")
                stored = media_storage.save_upload(upload, owner_id=7, album_id=11)
                path = media_storage.resolve_storage_path(stored["storage_path"])
                self.assertTrue(path.exists())
                self.assertEqual(stored["original_filename"], "Mi_foto.jpg")
                # El MIME servido es el CANONICO (derivado de la firma detectada),
                # nunca el que declaraba el cliente -- aunque coincidan aqui.
                self.assertEqual(stored["mime_type"], "image/jpeg")
                # Compare against the resolved tmp dir: on Windows, TemporaryDirectory()
                # can return a long-name path while Path.resolve() elsewhere follows the
                # 8.3 short-name alias (e.g. "ASUSVI~1"), which breaks a naive relative_to.
                self.assertEqual(path.parent.relative_to(Path(tmp).resolve()).as_posix(), "user_7/album_11")
                self.assertTrue(media_storage.remove_stored_file(stored["storage_path"]))
                self.assertFalse(path.exists())
                # La cuarentena no deja nada atras tras una promocion exitosa.
                self.assertEqual([], [p for p in Path(tmp_q).rglob("*") if p.is_file()])
            finally:
                media_storage._STORAGE_ROOT = original_root
                quarantine._QUARANTINE_ROOT = original_quarantine

    def test_rejects_storage_path_traversal(self):
        original_root = media_storage._STORAGE_ROOT
        with tempfile.TemporaryDirectory() as tmp:
            media_storage._STORAGE_ROOT = Path(tmp)
            try:
                with self.assertRaisesRegex(ValueError, "Ruta de archivo inválida"):
                    media_storage.resolve_storage_path("../../secrets.txt")
            finally:
                media_storage._STORAGE_ROOT = original_root


if __name__ == "__main__":
    unittest.main()
