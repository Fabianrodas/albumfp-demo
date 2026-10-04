"""Baseline del esquema actual.

Revision ID: 0001_current_schema_baseline
Revises:
Create Date: 2026-08-20

Esta revision no crea ni modifica nada a proposito: es solo un marcador.

El esquema que describe `schemas/schema.sql` ya existe en las bases de datos de
desarrollo, asi que la forma de adoptar Alembic sin tocar los datos es sellar
esa base con `alembic stamp 0001_current_schema_baseline`. A partir de aqui,
cada cambio de esquema es una revision nueva encadenada a esta.

Para una base vacia y desechable el camino sigue siendo `python -m
schemas.schema` (destructivo) seguido de `alembic stamp head`.
"""

revision = "0001_current_schema_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
