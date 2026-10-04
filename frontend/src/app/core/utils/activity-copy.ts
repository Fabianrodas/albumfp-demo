import { CAPABILITY_LABELS } from '../models/album-permissions';
import { ActivityEntry, AppNotification, PersonRef } from '../services/notifications';

/** El único sitio que convierte eventos en frases: nunca se pinta un
 * `event_type`, un id ni el JSON de metadatos. */
function who(person: PersonRef | null): string {
  return person ? (person.full_name || person.username) : 'Alguien';
}

export function activityText(entry: ActivityEntry): string {
  const actor = who(entry.actor);
  const target = entry.target ? who(entry.target) : null;
  switch (entry.event_type) {
    case 'album_invite_created':
      // Sin destinatario: una invitación por enlace todavía sin reclamar, o una
      // cuenta que ya no existe. La frase vale para los dos casos.
      return target ? `${actor} invitó a ${target}.` : `${actor} creó una invitación.`;
    case 'share_claimed': return `${actor} aceptó la invitación.`;
    case 'asset_uploaded': return `${actor} subió un recuerdo.`;
    case 'asset_added': return `${actor} añadió un recuerdo al álbum.`;
    case 'asset_removed': return `${actor} quitó un recuerdo del álbum.`;
    case 'cover_changed': return entry.details.cleared ? `${actor} quitó la portada.` : `${actor} cambió la portada.`;
    case 'share_permission_changed':
      return target ? `${actor} cambió los permisos de ${target}.` : `${actor} cambió los permisos de un acceso.`;
  }
}

/** El acceso concedido, con las mismas etiquetas que el diálogo de compartir. */
export function activityDetail(entry: ActivityEntry): string {
  if (entry.event_type !== 'album_invite_created' && entry.event_type !== 'share_permission_changed') return '';
  const capabilities = entry.details.capabilities ?? [];
  if (entry.details.permission !== 'write' || !capabilities.length) return 'Solo lectura';
  return `Puede: ${capabilities.map(c => CAPABILITY_LABELS[c]).join(' · ')}`;
}

export function notificationText(notice: AppNotification): string {
  const actor = who(notice.actor);
  const album = notice.album_title ? `«${notice.album_title}»` : 'un álbum que ya no existe';
  switch (notice.event_type) {
    case 'album_invite': return `${actor} te invitó a ${album}.`;
    case 'share_claimed': return `${actor} aceptó tu invitación a ${album}.`;
    case 'shared_album_upload': return `${actor} subió un recuerdo a ${album}.`;
  }
}

/** Destinos fijos por tipo: nunca una URL guardada ni un álbum borrado. */
export function notificationLink(notice: AppNotification): string {
  if (notice.event_type === 'album_invite') return '/compartido';
  return notice.album_id ? `/albumes/${notice.album_id}` : '/albumes';
}
