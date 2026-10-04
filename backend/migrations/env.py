"""Configuracion de Alembic para AlbumFP.

La conexion no se declara aqui ni en alembic.ini: se reutiliza el mismo engine
que usa la aplicacion (`app/db/db.py`), que ya construye la conexion a partir
del .env. Asi no existe una segunda copia de las credenciales que se pueda
quedar desincronizada, y `alembic.ini` no guarda ningun secreto.

`target_metadata` es None a proposito: el proyecto usa SQLAlchemy Core con SQL
explicito y no declara modelos, de modo que no hay metadata que comparar. Las
revisiones se escriben a mano; no hay autogenerate.
"""
from alembic import context

from app.db.db import engine


def run_migrations_online():
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
