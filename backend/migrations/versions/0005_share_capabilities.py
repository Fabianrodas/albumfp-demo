"""capacidades sueltas por colaborador

Revision ID: 0005_share_capabilities
Revises: 0004_share_permission_levels
Create Date: 2026-08-23

La revision 0004 introdujo un tercer nivel ('upload') entre 'read' y 'write'.
Probandolo, el dueno pidio otra cosa: no mas perfiles, sino DOS perfiles
(solo lectura y colaborador) y, dentro de colaborador, elegir una por una las
capacidades de escritura. Ver es siempre parte del acceso y no se puede
quitar, asi que no aparece como capacidad.

`capabilities` es TEXT[] y no una tabla aparte a proposito: son 5 valores
fijos que solo se leen junto con la fila del share (nunca se consultan
"todos los shares que pueden X"), asi que una tabla puente solo anadiria un
JOIN a la ruta mas caliente de la app, la comprobacion de permisos.

Datos existentes: un 'write' de antes podia hacerlo todo, asi que recibe
todas las capacidades; un 'upload' de la 0004 podia subir, editar y borrar
media, y pasa a 'write' con exactamente esas tres. Nadie pierde acceso.
"""
from alembic import op

revision = "0005_share_capabilities"
down_revision = "0004_share_permission_levels"
branch_labels = None
depends_on = None

TODAS = "ARRAY['upload','edit_media','delete_media','organize','edit_album']"
SOLO_MEDIA = "ARRAY['upload','edit_media','delete_media']"


def upgrade():
    op.execute("ALTER TABLE album_shares ADD COLUMN capabilities TEXT[] NOT NULL DEFAULT '{}'")
    op.execute(f"UPDATE album_shares SET capabilities = {TODAS} WHERE permission = 'write'")
    op.execute(f"UPDATE album_shares SET capabilities = {SOLO_MEDIA}, permission = 'write' WHERE permission = 'upload'")
    op.execute("ALTER TABLE album_shares DROP CONSTRAINT chk_permission")
    op.execute("ALTER TABLE album_shares ADD CONSTRAINT chk_permission CHECK (permission IN ('read', 'write'))")


def downgrade():
    # 'write' sin capacidades no existe en el modelo viejo; el mas parecido es
    # 'read', que es lo que de hecho podia hacer.
    op.execute("UPDATE album_shares SET permission = 'read' WHERE permission = 'write' AND cardinality(capabilities) = 0")
    op.execute("ALTER TABLE album_shares DROP COLUMN capabilities")
