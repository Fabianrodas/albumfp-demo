import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { ApiResponse } from './album-api';

export interface CommentAuthor {
  id: number;
  username: string;
  full_name: string;
  has_avatar: boolean;
}

/** Un comentario tal como lo devuelve el servidor (F04). `is_own` solo viene
 * en la ruta autenticada; en un enlace público no hay «propio». */
export interface AssetComment {
  id: number;
  asset_id: number;
  body: string;
  created_at: string;
  updated_at: string | null;
  edited: boolean;
  author: CommentAuthor;
  is_own?: boolean;
}

export const MAX_COMMENT_LENGTH = 2000;

/** Sin la memoria de `AlbumApi`: los comentarios los escriben varias cuentas,
 * así que una lectura memorizada se quedaría vieja sin que ninguna escritura
 * de esta pestaña la invalidara. */
@Injectable({ providedIn: 'root' })
export class CommentsApi {
  private readonly http = inject(HttpClient);

  list(mediaId: number, page: number, shareToken?: string) {
    const url = shareToken
      ? `/api/shared/${encodeURIComponent(shareToken)}/media/${mediaId}/comments`
      : `/api/media/${mediaId}/comments`;
    return this.http.get<ApiResponse<AssetComment[]>>(url, { params: { page: String(page), per_page: '20' } });
  }

  create(mediaId: number, body: string) {
    return this.http.post<ApiResponse<AssetComment>>(`/api/media/${mediaId}/comments`, { body });
  }

  update(commentId: number, body: string) {
    return this.http.patch<ApiResponse<AssetComment>>(`/api/comments/${commentId}`, { body });
  }

  remove(commentId: number) {
    return this.http.delete<ApiResponse<unknown>>(`/api/comments/${commentId}`);
  }
}
