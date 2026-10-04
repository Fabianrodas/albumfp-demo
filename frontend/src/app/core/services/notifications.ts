import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { AlbumCapability } from '../models/album-permissions';
import { ApiResponse } from './album-api';

/** Una cuenta tal como la nombran un aviso o la actividad; `null` si se borró. */
export interface PersonRef {
  id: number;
  username: string;
  full_name: string;
}

export type NotificationEvent = 'album_invite' | 'share_claimed' | 'shared_album_upload';

/** Un aviso in-app (L12). `album_id` es `null` cuando el álbum ya no existe. */
export interface AppNotification {
  id: number;
  event_type: NotificationEvent;
  album_id: number | null;
  album_title: string | null;
  actor: PersonRef | null;
  read_at: string | null;
  created_at: string;
}

export interface NotificationPreferences {
  notify_album_invites: boolean;
  notify_share_claimed: boolean;
  notify_shared_album_uploads: boolean;
}

export type ActivityEvent =
  | 'album_invite_created' | 'share_claimed' | 'asset_uploaded' | 'asset_added'
  | 'asset_removed' | 'cover_changed' | 'share_permission_changed';

/** Un evento de la actividad de un álbum normal. Del recuerdo solo llegan su
 * tipo y si sigue en el álbum: nunca su título ni su leyenda. */
export interface ActivityEntry {
  id: number;
  event_type: ActivityEvent;
  actor: PersonRef | null;
  target: PersonRef | null;
  subject: { id: number; file_type: 'image' | 'video'; available: boolean } | null;
  details: { permission?: 'read' | 'write'; capabilities?: AlbumCapability[]; cleared?: boolean };
  created_at: string;
}

export const NOTIFICATIONS_PAGE = 20;

/**
 * La campana y la actividad. A propósito SIN la memoria de `AlbumApi`: lo que
 * cambia aquí lo escribe otra cuenta (una invitación, una subida), así que una
 * respuesta memorizada enseñaría una campana vieja hasta la siguiente escritura
 * propia. Solo in-app: ni permisos del navegador ni push.
 */
@Injectable({ providedIn: 'root' })
export class NotificationsApi {
  private readonly http = inject(HttpClient);

  list(page: number) {
    return this.http.get<ApiResponse<AppNotification[]>>('/api/notifications',
      { params: { page: String(page), per_page: String(NOTIFICATIONS_PAGE) } });
  }
  unreadCount() { return this.http.get<ApiResponse<{ unread: number }>>('/api/notifications/unread-count'); }
  markRead(id: number) {
    return this.http.patch<ApiResponse<{ id: number; read_at: string }>>(`/api/notifications/${id}/read`, {});
  }
  markAllRead() { return this.http.post<ApiResponse<{ updated: number }>>('/api/notifications/read-all', {}); }

  preferences() { return this.http.get<ApiResponse<NotificationPreferences>>('/api/notifications/preferences'); }
  updatePreferences(patch: Partial<NotificationPreferences>) {
    return this.http.patch<ApiResponse<NotificationPreferences>>('/api/notifications/preferences', patch);
  }

  albumActivity(albumId: number, page: number) {
    return this.http.get<ApiResponse<ActivityEntry[]>>(`/api/albums/${albumId}/activity`,
      { params: { page: String(page), per_page: String(NOTIFICATIONS_PAGE) } });
  }
}
