"""Create missing image previews in the local Demo library.

This maintenance command is explicit and does not run at application startup.
It adds preview files and fills metadata that is currently empty; originals
remain unchanged. Re-running it skips rows that already have a preview.

Run from the backend directory:

    python -m scripts.backfill_previews
    python -m scripts.backfill_previews --user 3
    python -m scripts.backfill_previews --limit 50
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.db import db_conn
from app.media.previews import create_preview_for_stored
from app.utils.sql_security import execute_safe


def pendientes(conn, user_id: int | None, limit: int | None):
    filtro = "AND m.user_id = :user_id" if user_id else ""
    tope = "LIMIT :limit" if limit else ""
    return execute_safe(
        conn,
        f"""
        SELECT m.id, m.storage_path
        FROM assets m
        JOIN media_metadata mm ON mm.media_id = m.id
        WHERE m.file_type = 'image'
          AND mm.preview_storage_path IS NULL
          {filtro}
        ORDER BY m.id
        {tope}
        """,
        {k: v for k, v in {"user_id": user_id, "limit": limit}.items() if v},
    ).mappings().all()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", type=int, default=None, help="solo las fotos de esta cuenta")
    parser.add_argument("--limit", type=int, default=None, help="procesar como mucho N fotos")
    args = parser.parse_args()

    from app.storage.contracts import StorageError
    from app.storage.media_storage import remove_stored_file

    with db_conn() as conn:
        filas = pendientes(conn, args.user, args.limit)

    if not filas:
        print("Nada que hacer: todas las fotos ya tienen vista previa.")
        return 0

    print(f"{len(filas)} foto(s) sin vista previa.")
    hechas = fallidas = 0
    for fila in filas:
        try:
            preview = create_preview_for_stored(fila["storage_path"])
        except StorageError as exc:
            print(f"  ! almacenamiento no disponible: {exc}")
            print(f"Interrumpido: {hechas} generada(s) antes del fallo.")
            return 1
        if not preview:
            fallidas += 1
            print(f"  - media {fila['id']}: no se pudo generar (se seguira sirviendo el original)")
            continue
        # Una transaccion por foto: parar a mitad deja hecho lo hecho, y
        # volver a lanzar retoma donde iba en vez de repetirlo todo.
        # El UPDATE es condicional sobre preview_storage_path IS NULL: dos
        # backfills a la vez (o un backfill y una subida nueva) no deben
        # pisar una vista previa que ya ganó la carrera.
        with db_conn() as conn:
            resultado = execute_safe(
                conn,
                """
                UPDATE media_metadata
                SET preview_storage_path = :storage_path,
                    preview_mime_type = :mime_type,
                    preview_width = :width,
                    preview_height = :height,
                    preview_file_size = :file_size
                WHERE media_id = :media_id AND preview_storage_path IS NULL
                """,
                {"media_id": fila["id"], **preview},
            )
            gano = resultado.rowcount == 1
        if gano:
            hechas += 1
        else:
            # Otro backfill llegó antes, o la media desapareció. La preview
            # propia se compensa: nunca se sobrescribe la ganadora.
            print(f"  - media {fila['id']}: otra ejecución ganó la carrera; se descarta la copia")
            try:
                remove_stored_file(preview["storage_path"])
            except StorageError:
                pass
            fallidas += 1

    print(f"Listo: {hechas} generada(s), {fallidas} sin generar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
