from pathlib import Path
from sqlalchemy import text
from app.db.db import engine

SQL_FILE = Path(__file__).with_name("schema.sql")

DROP_ALL_TABLES = """
DO $$ DECLARE
    r RECORD;
BEGIN
    -- Eliminar todas las tablas del schema public
    FOR r IN (
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
    )
    LOOP
        EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE';
    END LOOP;

    -- Eliminar todos los tipos ENUM creados por el usuario
    FOR r IN (
        SELECT typname
        FROM pg_type
        WHERE typnamespace = (
            SELECT oid FROM pg_namespace WHERE nspname = 'public'
        )
        AND typtype = 'e'
    )
    LOOP
        EXECUTE 'DROP TYPE IF EXISTS public.' || quote_ident(r.typname) || ' CASCADE';
    END LOOP;

END $$;
"""


def run_schema(reset: bool = True):
    sql = SQL_FILE.read_text(encoding="utf-8")

    with engine.connect() as conn:

        if reset:
            conn.execute(text(DROP_ALL_TABLES))
            conn.commit()
            # Sin caracteres fuera de ASCII: la consola de Windows usa cp1252 y
            # un print de "✔" lanzaba UnicodeEncodeError justo después del DROP,
            # dejando la base vacía y sin volver a crear el esquema.
            print("OK - Base limpiada")

        conn.execute(text(sql))
        conn.commit()

    print("OK - Schema aplicado correctamente")


if __name__ == "__main__":
    run_schema(reset=True)
