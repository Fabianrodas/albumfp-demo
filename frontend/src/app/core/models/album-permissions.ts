/** Dos perfiles de acceso, no una escala: `read` ve, `write` es un
 * colaborador cuyo alcance exacto lo dicen sus capacidades. `owner` no se
 * concede nunca por compartición — solo lo tiene quien creó el álbum. */
export type AlbumRole = 'owner' | 'read' | 'write';

/** Capacidades de ESCRITURA, concedidas una por una. Ver no está aquí: es
 * parte de tener acceso y no se puede quitar. Mismo orden y mismos nombres
 * que ALBUM_CAPABILITIES en el backend (app/domain/rules.py). */
export type AlbumCapability = 'upload' | 'edit_media' | 'delete_media' | 'organize' | 'edit_album';

export const ALBUM_CAPABILITIES: AlbumCapability[] = ['upload', 'edit_media', 'delete_media', 'organize', 'edit_album'];

/** Etiqueta y explicación de cada capacidad. Una sola fuente para el modal de
 * permisos, la lista de colaboradores y cualquier resumen. */
export const CAPABILITY_LABELS: Record<AlbumCapability, string> = {
  upload: 'Subir fotos y videos',
  edit_media: 'Editar fotos',
  delete_media: 'Enviar fotos a la papelera',
  organize: 'Marcar favoritos y usar etiquetas',
  edit_album: 'Cambiar los ajustes del álbum',
};

export const CAPABILITY_HINTS: Record<AlbumCapability, string> = {
  upload: 'Añadir archivos nuevos al álbum',
  edit_media: 'Cambiar título, descripción, fecha y lugar',
  delete_media: 'Se pueden recuperar durante 30 días',
  organize: '',
  edit_album: 'Título, descripción, portada y privacidad',
};

/**
 * Única puerta del frontend. El dueño puede todo; un colaborador solo lo que
 * le concedieron. Espeja `require_album_capability` del backend, que es quien
 * de verdad decide — esto solo evita enseñar botones que darían 403.
 */
export const hasCapability = (role: AlbumRole | undefined, capabilities: readonly string[] | undefined, capability: AlbumCapability) =>
  role === 'owner' || !!capabilities?.includes(capability);

/** Compartir, ver/vaciar la papelera del álbum y desactivarlo: exclusivo del
 * dueño real. Ninguna capacidad lo alcanza, a propósito. */
export const canManageAlbum = (role: AlbumRole | undefined) => role === 'owner';

export const canUploadMedia = (role: AlbumRole | undefined, capabilities?: readonly string[]) => hasCapability(role, capabilities, 'upload');
export const canEditMedia = (role: AlbumRole | undefined, capabilities?: readonly string[]) => hasCapability(role, capabilities, 'edit_media');
export const canDeleteMedia = (role: AlbumRole | undefined, capabilities?: readonly string[]) => hasCapability(role, capabilities, 'delete_media');
export const canOrganizeMedia = (role: AlbumRole | undefined, capabilities?: readonly string[]) => hasCapability(role, capabilities, 'organize');
export const canEditAlbum = (role: AlbumRole | undefined, capabilities?: readonly string[]) => hasCapability(role, capabilities, 'edit_album');
