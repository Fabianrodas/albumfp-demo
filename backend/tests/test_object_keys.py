"""Canonical storage-key grammar and key hashing (spec section 16)."""
import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage.object_keys import (  # noqa: E402
    InvalidStorageKey,
    KEY_MAX_BYTES,
    build_object_key,
    key_hash,
    validate_storage_key,
)


VALID_KEY = "user_1/album_2/0123456789abcdef0123456789abcdef.jpg"
AVATAR_KEY = "user_42/avatar/ffffffffffffffffffffffffffffffff.png"


class ValidKeysTests(unittest.TestCase):
    def test_album_key_is_accepted_unchanged(self):
        self.assertEqual(VALID_KEY, validate_storage_key(VALID_KEY))

    def test_avatar_key_is_accepted(self):
        self.assertEqual(AVATAR_KEY, validate_storage_key(AVATAR_KEY))

    def test_all_product_extensions_are_accepted(self):
        for extension in ("jpg", "png", "gif", "webp", "avif", "heic", "heif", "mp4", "mov", "webm", "ogg"):
            with self.subTest(extension=extension):
                key = f"user_1/album_2/{'a' * 32}.{extension}"
                self.assertEqual(key, validate_storage_key(key))


class RejectedKeysTests(unittest.TestCase):
    def test_traversal_is_rejected(self):
        with self.assertRaises(InvalidStorageKey):
            validate_storage_key("user_1/../etc/passwd")

    def test_percent_encoded_traversal_is_rejected_without_decoding(self):
        with self.assertRaises(InvalidStorageKey):
            validate_storage_key("user_1/%2e%2e%2fetc/passwd")

    def test_any_percent_is_rejected(self):
        with self.assertRaises(InvalidStorageKey):
            validate_storage_key("user_1/album_2/%41" + "a" * 29 + ".jpg")

    def test_absolute_and_windows_paths_are_rejected(self):
        for key in (
            "/user_1/album_2/" + "a" * 32 + ".jpg",
            "C:/user_1/album_2/" + "a" * 32 + ".jpg",
            "user_1\\album_2\\" + "a" * 32 + ".jpg",
        ):
            with self.subTest(key=key):
                with self.assertRaises(InvalidStorageKey):
                    validate_storage_key(key)

    def test_nul_and_controls_are_rejected(self):
        for control in ("\x00", "\n", "\r", "\t"):
            with self.subTest(control=repr(control)):
                with self.assertRaises(InvalidStorageKey):
                    validate_storage_key("user_1/album_2/" + "a" * 32 + f".jpg{control}")

    def test_wrong_component_count_is_rejected(self):
        for key in (
            "user_1/" + "a" * 32 + ".jpg",
            "user_1/album_2/sub/" + "a" * 32 + ".jpg",
            "user_1//album_2/" + "a" * 32 + ".jpg",
            "user_1/album_2/" + "a" * 32 + ".jpg/",
        ):
            with self.subTest(key=key):
                with self.assertRaises(InvalidStorageKey):
                    validate_storage_key(key)

    def test_uppercase_uuid_extension_scope_unicode_and_space_are_rejected(self):
        invalid = (
            "user_1/album_2/" + "A" * 32 + ".jpg",
            "user_1/album_2/" + "a" * 32 + ".PHP",
            "user_1/backups/" + "a" * 32 + ".jpg",
            "user_1/álbum_2/" + "a" * 32 + ".jpg",
            "user_1/album 2/" + "a" * 32 + ".jpg",
        )
        for key in invalid:
            with self.subTest(key=key):
                with self.assertRaises(InvalidStorageKey):
                    validate_storage_key(key)

    def test_lowercase_unsupported_extension_is_rejected(self):
        with self.assertRaises(InvalidStorageKey):
            validate_storage_key("user_1/album_2/" + "a" * 32 + ".php")

    def test_lone_surrogate_is_rejected_as_invalid_storage_key(self):
        with self.assertRaises(InvalidStorageKey):
            validate_storage_key("user_1/album_2/" + "a" * 32 + ".jpg" + "\ud800")

    def test_non_text_empty_and_noncanonical_ids_are_rejected(self):
        invalid = (
            "",
            None,
            5,
            b"user_1/album_2/x.jpg",
            "user_0/album_2/" + "a" * 32 + ".jpg",
            "user_01/album_2/" + "a" * 32 + ".jpg",
            "user_1/album_0/" + "a" * 32 + ".jpg",
            "user_1/album_01/" + "a" * 32 + ".jpg",
            "user_" + "9" * 20 + "/album_2/" + "a" * 32 + ".jpg",
        )
        for key in invalid:
            with self.subTest(key=key):
                with self.assertRaises(InvalidStorageKey):
                    validate_storage_key(key)

    def test_boolean_owner_spelling_is_rejected(self):
        with self.assertRaises(InvalidStorageKey):
            validate_storage_key("user_True/album_2/" + "a" * 32 + ".jpg")

    def test_key_over_128_bytes_is_rejected(self):
        self.assertEqual(128, KEY_MAX_BYTES)
        key = "user_" + "9" * 19 + "/album_" + "9" * 19 + "/" + "a" * 32 + ".webp"
        self.assertEqual(88, len(key.encode("ascii")))
        self.assertEqual(key, validate_storage_key(key))
        too_long = key + "x" * (129 - len(key))
        self.assertEqual(129, len(too_long.encode("ascii")))
        with self.assertRaises(InvalidStorageKey):
            validate_storage_key(too_long)


class KeyHashTests(unittest.TestCase):
    def test_hash_is_stable_16_lowercase_hex_and_key_free(self):
        result = key_hash(VALID_KEY)
        self.assertEqual(hashlib.sha256(VALID_KEY.encode("utf-8")).hexdigest()[:16], result)
        self.assertRegex(result, r"^[0-9a-f]{16}$")
        self.assertEqual(result, key_hash(VALID_KEY))
        self.assertNotEqual(result, key_hash(AVATAR_KEY))
        self.assertNotIn("user_1", result)

    def test_invalid_key_can_be_hashed_for_incident_logging(self):
        self.assertRegex(key_hash("../etc/passwd"), r"^[0-9a-f]{16}$")


class BuildObjectKeyTests(unittest.TestCase):
    def test_builds_album_and_avatar_keys(self):
        for scope, extension in (("album_3", "jpg"), ("avatar", "png")):
            with self.subTest(scope=scope):
                key = build_object_key(7, scope, extension)
                self.assertEqual(key, validate_storage_key(key))
                self.assertTrue(key.startswith(f"user_7/{scope}/"))

    def test_two_calls_do_not_repeat_a_key(self):
        self.assertNotEqual(build_object_key(7, "album_3", "jpg"), build_object_key(7, "album_3", "jpg"))

    def test_invalid_scope_extension_and_owner_are_rejected(self):
        for args in ((7, "../etc", "jpg"), (7, "album_3", "php"), (0, "album_3", "jpg"), (True, "album_3", "jpg")):
            with self.subTest(args=args):
                with self.assertRaises(InvalidStorageKey):
                    build_object_key(*args)


if __name__ == "__main__":
    unittest.main()
