"""Create synthetic local-only content for AlbumFP Demo screenshots."""

from __future__ import annotations

import argparse
import getpass
import io
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPOSITORY_ROOT / ".env", override=False)

from app.synthetic_gallery_seed import SYNTHETIC_USERNAME, run_seed  # noqa: E402


def read_password_stdin(stream: io.TextIOBase) -> tuple[str, str]:
    """Read two non-echoed password lines for explicitly scripted runs."""
    first = stream.readline()
    second = stream.readline()
    if not first or not second:
        raise ValueError("Se requieren dos líneas de contraseña por stdin")
    return first.rstrip("\r\n"), second.rstrip("\r\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a synthetic account, album, and EXIF-free images in the local Demo database."
    )
    parser.add_argument(
        "--confirm-local-demo",
        action="store_true",
        required=True,
        help="confirm this command is intended for the local albumfp_demo database",
    )
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password twice from stdin without echoing it",
    )
    args = parser.parse_args()
    if not args.confirm_local_demo:
        parser.error("--confirm-local-demo is required")

    if args.password_stdin:
        try:
            password, confirmation = read_password_stdin(sys.stdin)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        password = getpass.getpass(f"Choose a local password for {SYNTHETIC_USERNAME}: ")
        confirmation = getpass.getpass("Repeat the local password: ")
    if password != confirmation:
        parser.error("The passwords do not match")

    try:
        album_id, image_count = run_seed(password)
    except (ValueError, RuntimeError) as exc:
        print(f"Seed detenido: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Database driver exceptions can include connection URLs. Never print
        # raw technical details that could disclose local configuration.
        print(f"Seed detenido por un error técnico ({type(exc).__name__}).", file=sys.stderr)
        return 1

    print(f"Cuenta sintética: {SYNTHETIC_USERNAME}")
    print(f"Álbum sintético creado: {album_id}; imágenes: {image_count}")
    print("Archivos guardados por la aplicación en el almacenamiento local.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
