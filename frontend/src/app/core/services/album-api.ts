import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpEvent, HttpEventType } from '@angular/common/http';
import { Observable, catchError, of, shareReplay, tap, throwError } from 'rxjs';
import { AlbumCapability, AlbumRole } from '../models/album-permissions';

// ponytail: presupuesto fijo; a ~141 KB por preview (medido) son ~340 fotos,
// unas seis páginas de álbum. Subirlo si alguien navega bibliotecas enteras.
export const PREVIEW_CACHE_BYTES = 48 * 1024 * 1024;

export interface ApiResponse<T> {
  data: T;
  message: string;
  /** El backend anida los extras en `meta` (`ok(..., pagination=...)`), nunca
   * en el primer nivel: leer `response.pagination` daba siempre `undefined`. */
  meta?: { pagination?: { page: number; per_page: number; total: number; total_pages: number } };
}

export interface Album {
  id: number;
  user_id: number;
  titulo: string;
  descripcion?: string | null;
  is_private: boolean;
  cover_media_id?: number | null;
  cover_file_type?: 'image' | 'video' | null;
  role: AlbumRole;
  /** Solo lo devuelve el detalle del álbum, no los listados: los listados
   * únicamente pintan la insignia del rol, no botones de escritura. */
  capabilities?: AlbumCapability[];
  /** L12, solo en el detalle: dueño o colaborador con acceso de cuenta. Quien
   * solo LEE un álbum público no ve su historial de colaboración. */
  can_view_activity?: boolean;
  created_at: string;
  updated_at?: string | null;
}

/** Dueño de un álbum público, tal como lo muestran las tarjetas del inicio. */
export interface AlbumOwner {
  id: number;
  username: string;
  full_name: string;
  has_avatar: boolean;
}

/** Álbum del feed público: sin `role` ni `is_private`, con su dueño. */
export interface PublicAlbum {
  id: number;
  titulo: string;
  descripcion?: string | null;
  cover_media_id?: number | null;
  cover_file_type?: 'image' | 'video' | null;
  created_at: string;
  owner: AlbumOwner;
}

/** Una foto o video del feed público: como `Media`, pero con su dueño (para
 * el chip de autor) y sin nada de lo que un colaborador necesitaría. */
export interface PublicMedia {
  id: number;
  /** No nulo por el propio filtro del feed: solo lista assets que están en un
   * álbum público activo, y el contexto se resuelve con ese mismo predicado. */
  album_id: number;
  file_type: 'image' | 'video';
  title?: string | null;
  caption?: string | null;
  taken_at?: string | null;
  created_at: string;
  owner: AlbumOwner;
}

export interface UserSummary {
  id: number;
  username: string;
  full_name: string;
  has_avatar: boolean;
  created_at: string;
  public_albums: number;
}

export interface UserProfile extends UserSummary {
  public_media: number;
}

export interface Media {
  id: number;
  /** El álbum de contexto. `null` solo para un recuerdo que no está en ningún
   * álbum (L10B): su detalle vive en `/recuerdos/:id`. */
  album_id: number | null;
  file_type: 'image' | 'video';
  title?: string | null;
  caption?: string | null;
  is_favorite?: boolean;
  taken_at?: string | null;
  created_at?: string;
  created_by?: number | null;
  created_by_username?: string | null;
  deleted_at?: string | null;
  /** Archivado: fuera de Biblioteca e Inicio, pero no en la papelera. */
  archived_at?: string | null;
  purge_at?: string | null;
  /** Contexto de presentación, no identidad: lo traen la búsqueda global y
   * los listados globales, y es `null` para un recuerdo sin álbum (L10B). */
  album_titulo?: string | null;
}

/** Un álbum del dueño que contiene el recuerdo. */
export interface MediaAlbumRef {
  id: number;
  titulo: string;
}

export interface MediaDetailData {
  media: Media;
  metadata?: MediaMetadata | null;
  exif?: MediaExif | null;
  tags: Tag[];
  album_role: AlbumRole;
  album_capabilities: AlbumCapability[];
  albums?: MediaAlbumRef[];
}

/** L11: la definición guardada de un álbum inteligente. Las mismas claves que
 * la búsqueda global, ya tipadas; orden y página no forman parte de ella. */
export interface SmartAlbumFilters {
  q?: string;
  media_type?: 'image' | 'video';
  date_from?: string;
  date_to?: string;
  year?: number;
  album_id?: number;
  favorite?: boolean;
  tag_id?: number;
  place?: string;
  archived?: 'only' | 'exclude';
}

/** Los nombres que el servidor resolvió para el dueño; `null` si el id guardado
 * ya no existe o ya no es suyo. */
export interface SmartAlbumReferences {
  album: { id: number; titulo: string } | null;
  tag: { id: number; name: string } | null;
}

/** Una búsqueda guardada, NO un álbum: tipo aparte a propósito, para que nada
 * que espere un `Album` (subir, pertenencias, compartir, portada) lo acepte. */
export interface SmartAlbum {
  id: number;
  titulo: string;
  descripcion?: string | null;
  filters: SmartAlbumFilters;
  created_at: string;
  updated_at?: string | null;
  references: SmartAlbumReferences;
  stale_references: ('album_id' | 'tag_id')[];
}

export interface SmartAlbumInput {
  titulo: string;
  descripcion: string | null;
  filters: SmartAlbumFilters;
}

/** Media owned by the current user, enriched for the global library timeline. */
export interface LibraryMedia extends Media {
  album_titulo: string | null;
  effective_date: string;
}

export interface LibraryResponse {
  data: LibraryMedia[];
  message: string;
  meta: { next_cursor: string | null; total: number; limit: number };
}

export interface HomeMemory extends Media {
  user_id: number;
  album_titulo: string | null;
  memory_year: number;
  years_ago: number;
  year_total: number;
}

/** F02: lo personal que queda en Inicio. Lo reciente vive en Biblioteca
 * (`libraryRecent`), los álbumes en Mis álbumes y lo compartido en Compartido. */
export interface PersonalHome {
  local_date: string;
  on_this_day: { items: HomeMemory[]; total: number; per_year_limit: number };
}

export interface MediaMetadata {
  id: number;
  media_id: number;
  file_size?: number | null;
  resolution?: string | null;
  duration?: number | null;
  format?: string | null;
  original_filename?: string | null;
}

/** Metadatos EXIF que el backend lee de la foto. Solo las fotos los tienen. */
export interface MediaExif {
  taken_at_original?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  altitude_m?: number | null;
  camera_make?: string | null;
  camera_model?: string | null;
  orientation?: number | null;
}

/** Lugar derivado de las coordenadas EXIF por LocationIQ, solo cuando el
 * dueño pidio completar el contexto. Null en cualquier campo que el
 * proveedor no haya devuelto para esa foto. */
/** Texto detectado dentro de una foto. `detected_language` viene siempre null:
 * OCR.Space no devuelve el idioma que reconoció. */
export interface MediaOcr {
  media_id: number;
  extracted_text: string;
  detected_language?: string | null;
  provider: string;
  analyzed_at: string;
}

/**
 * Lo que devuelve el detalle de un enlace público: la misma forma que
 * `mediaDetail()` más el contexto y el OCR, que en la ruta autenticada son
 * dos peticiones aparte (un visitante anónimo no tiene sesión con la que
 * encadenarlas). `album_role` siempre llega como 'read' y sin capacidades.
 */
export interface SharedMediaDetail {
  media: Media;
  metadata?: MediaMetadata | null;
  exif?: MediaExif | null;
  tags: Tag[];
  context?: MediaContext | null;
  ocr?: MediaOcr | null;
  album_role: AlbumRole;
  album_capabilities: AlbumCapability[];
  allow_original_download: boolean;
  show_metadata: boolean;
}

export interface MediaContext {
  place_display_name?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  locality?: string | null;
  region?: string | null;
  country_code?: string | null;
  country_name?: string | null;
  timezone?: string | null;
  location_provider?: string | null;
  location_enriched_at?: string | null;
  /** Hora local de reloj, ya convertida en el servidor: se pinta tal cual. */
  sunrise_at?: string | null;
  sunset_at?: string | null;
  solar_provider?: string | null;
  solar_enriched_at?: string | null;
  /** Null con `holiday_enriched_at` puesto significa "ese día no fue
   * festivo", que es un resultado, no un dato que falte. */
  holiday_name?: string | null;
  holiday_type?: string | null;
  holiday_provider?: string | null;
  holiday_enriched_at?: string | null;
  weather_temp_c?: number | null;
  weather_condition?: string | null;
  weather_icon?: string | null;
  weather_precip_mm?: number | null;
  weather_wind_kph?: number | null;
  weather_provider?: string | null;
  weather_enriched_at?: string | null;
}

/** Qué pudo completar cada proveedor. `cached` y `not_configured` no son
 * fallos: son razones legítimas para no haber salido a la red. */
export interface ContextResults {
  location?: string;
  solar?: string;
  holiday?: string;
  weather?: string;
}

/** Un grupo de la vista Lugares: recuerdos agrupados por donde ocurrieron.
 * `locality` puede ser null (agrupado por región/país) — nunca se inventa una ciudad. */
export interface Place {
  country_code: string | null;
  country_name: string | null;
  locality: string | null;
  region: string | null;
  media_count: number;
  cover_media_id: number | null;
  cover_file_type: 'image' | 'video' | null;
}

export interface Tag { id: number; name: string; }

/** Etiqueta propuesta por Imagga. `tag_id` no nulo significa que esa etiqueta
 * YA existe en la biblioteca: añadirla no crea nada, solo la asigna. */
export interface TagSuggestion { name: string; confidence: number; tag_id: number | null; }

/** Dos perfiles: ver, o colaborar con capacidades concretas. */
export type SharePermission = 'read' | 'write';

export interface Share {
  id: number;
  album_id?: number;
  permission: SharePermission;
  /** Vacío cuando `permission` es 'read'. */
  capabilities: AlbumCapability[];
  shared_with_user_id?: number | null;
  shared_with_username?: string | null;
  shared_with_full_name?: string | null;
  /** Solo en la respuesta de creación -- la base ya solo guarda su hash. */
  token?: string | null;
  /** Si este acceso todavía tiene un enlace vivo (una invitación ya reclamada
   * no lo tiene). Es un booleano, nunca el token. */
  has_token?: boolean;
  /** Si ese enlace vivo se puede volver a MOSTRAR (cifrado reversible, no
   * solo el hash de siempre). Falso en un enlace creado antes de esta
   * función, o si el servidor no tenía la clave configurada en ese momento
   * -- regenerarlo una vez lo pone en true. */
  can_reveal?: boolean;
  share_type?: 'account' | 'public_link';
  claimed_at?: string | null;
  active: boolean;
  expires_at?: string | null;
  created_at?: string;
  /** Exclusivos de un enlace público (share_type === 'public_link'). */
  has_password?: boolean;
  allow_original_download?: boolean;
  show_metadata?: boolean;
  /** Último acceso -- nunca IP ni ubicación, mismo criterio que las sesiones. */
  last_accessed_at?: string | null;
  last_accessed_user_agent?: string | null;
}

export interface MediaUploadPayload {
  file: File;
  /** Solo se envía después de que el usuario confirme un conflicto exacto. */
  force_duplicate?: boolean;
  title?: string | null;
  caption?: string | null;
  is_favorite?: boolean;
  taken_at?: string | null;
  resolution?: string | null;
  duration?: number | null;
  tag_ids?: number[];
  /** Pin puesto a mano en el mapa: gana sobre el GPS del EXIF, igual que `taken_at`. */
  latitude?: number | null;
  longitude?: number | null;
}

/** Edición post-subida: solo lo que `PATCH /api/media/:id` acepta. Favoritos
 * y tags tienen su propio endpoint y no entran aquí.
 *
 * `taken_at: null` y `clear_location: true` no borran: devuelven el mando a
 * lo que trae la foto (EXIF), igual que subirla sin escribir esos campos. */
export interface MediaUpdatePayload {
  title?: string | null;
  caption?: string | null;
  taken_at?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  clear_location?: boolean;
}

export interface AlbumUpdate {
  titulo?: string;
  descripcion?: string | null;
  is_private?: boolean;
  cover_media_id?: number | null;
}

const API = '';

@Injectable({ providedIn: 'root' })
export class AlbumApi {
  private http = inject(HttpClient);

  /**
   * Lecturas ya resueltas, por URL. Sin esto, cada visita a una página vuelve a
   * pedir lo mismo y la vista parpadea vacía medio segundo antes de pintar.
   */
  private readonly reads = new Map<string, Observable<unknown>>();

  /** Descarta todo lo memorizado. Cualquier escritura la llama. */
  invalidate() { this.reads.clear(); }

  /**
   * Vistas previas ya descargadas, por id, solo en la memoria de esta pestaña
   * (nada de Cache Storage ni disco). Medido en producción: cada vuelta a un
   * álbum volvía a bajar cada preview, ~141 KB y ~750 ms por el tramo
   * Rack→Dell. Los bytes de una preview no cambian para un id, así que las
   * escrituras no la invalidan; el fin de sesión sí (`forgetSession`). Cada
   * consumidor crea y revoca su propia object URL sobre el mismo Blob.
   */
  private readonly previews = new Map<number, Blob>();
  private previewBytes = 0;

  /** Todo lo que pertenece a la cuenta que se va: lecturas y vistas previas. */
  forgetSession() {
    this.invalidate();
    this.previews.clear();
    this.previewBytes = 0;
  }

  private keepPreview(id: number, blob: Blob) {
    // Un original de respaldo enorme (video de portada) no entra: vaciaría el
    // presupuesto entero para guardar una sola cosa.
    if (blob.size > PREVIEW_CACHE_BYTES / 4) return;
    const previous = this.previews.get(id);
    if (previous) { this.previews.delete(id); this.previewBytes -= previous.size; }
    this.previews.set(id, blob);
    this.previewBytes += blob.size;
    // El Map conserva el orden de inserción: el primero es el menos reciente.
    for (const [oldest, old] of this.previews) {
      if (this.previewBytes <= PREVIEW_CACHE_BYTES) break;
      this.previews.delete(oldest);
      this.previewBytes -= old.size;
    }
  }

  private read<T>(path: string, params?: Record<string, string>): Observable<T> {
    const query = params ? new URLSearchParams(params).toString() : '';
    const key = query ? `${path}?${query}` : path;

    const hit = this.reads.get(key) as Observable<T> | undefined;
    if (hit) return hit;

    const shared = this.http.get<T>(`${API}${path}`, params ? { params } : {}).pipe(
      // Un error no debe quedar memorizado: se descarta la entrada para que el
      // siguiente intento vuelva a pedirlo de verdad.
      catchError(error => { this.reads.delete(key); return throwError(() => error); }),
      shareReplay({ bufferSize: 1, refCount: false }),
    );
    this.reads.set(key, shared);
    return shared;
  }

  /**
   * Envuelve una escritura. Invalidar la caché entera es más burdo que hacerlo
   * por clave, pero es lo único que no puede devolver datos viejos, y aquí se
   * lee muchísimo más de lo que se escribe.
   */
  private write<T>(request: Observable<T>): Observable<T> {
    return request.pipe(tap(() => this.invalidate()));
  }

  private uploadForm(payload: MediaUploadPayload) {
    const form = new FormData();
    form.append('file', payload.file, payload.file.name);
    if (payload.title) form.append('title', payload.title);
    if (payload.caption) form.append('caption', payload.caption);
    if (payload.taken_at) form.append('taken_at', payload.taken_at);
    form.append('is_favorite', payload.is_favorite ? 'true' : 'false');
    if (payload.force_duplicate === true) form.append('force_duplicate', 'true');
    if (payload.resolution) form.append('resolution', payload.resolution);
    if (payload.duration != null) form.append('duration', String(payload.duration));
    if (payload.tag_ids?.length) form.append('tag_ids', payload.tag_ids.join(','));
    if (payload.latitude != null) form.append('latitude', String(payload.latitude));
    if (payload.longitude != null) form.append('longitude', String(payload.longitude));
    return form;
  }

  /** `registration_mode` no es un secreto (S03): el formulario de registro
   * lo lee ANTES de tener sesión para saber que pintar. El backend sigue
   * siendo la autoridad real -- esto solo evita mostrar un formulario que el
   * servidor va a rechazar de todos modos. */
  health() { return this.read<ApiResponse<{ status: string; registration_mode: 'open' | 'invite_only' | 'closed' }>>('/api/health'); }

  albums(params?: Record<string, string>) { return this.read<ApiResponse<Album[]>>('/api/albums', params); }
  publicMedia(params?: Record<string, string>) { return this.read<ApiResponse<PublicMedia[]>>('/api/media/public', params); }
  home(localDate: string) { return this.read<ApiResponse<PersonalHome>>('/api/home', { local_date: localDate }); }
  album(id: number) { return this.read<ApiResponse<Album>>(`/api/albums/${id}`); }
  albumStats(id: number) { return this.read<ApiResponse<{ total_media: number; total_favorites: number; total_file_size: number; role: AlbumRole }>>(`/api/albums/${id}/stats`); }
  /** Sin cache, como mediaFile: es un blob binario, no una respuesta JSON. */
  downloadAlbum(id: number) { return this.http.get(`${API}/api/albums/${id}/download`, { responseType: 'blob' }); }
  createAlbum(body: Pick<Album, 'titulo' | 'descripcion' | 'is_private'>) { return this.write(this.http.post<ApiResponse<Album>>(`${API}/api/albums`, body)); }
  updateAlbum(id: number, body: AlbumUpdate) { return this.write(this.http.patch<ApiResponse<Album>>(`${API}/api/albums/${id}`, body)); }
  smartAlbums(params?: Record<string, string>) { return this.read<ApiResponse<SmartAlbum[]>>('/api/smart-albums', params); }
  smartAlbum(id: number) { return this.read<ApiResponse<SmartAlbum>>(`/api/smart-albums/${id}`); }
  smartAlbumMedia(id: number, params?: Record<string, string>) {
    return this.read<ApiResponse<Media[]>>(`/api/smart-albums/${id}/media`, params);
  }
  createSmartAlbum(body: SmartAlbumInput) { return this.write(this.http.post<ApiResponse<SmartAlbum>>(`${API}/api/smart-albums`, body)); }
  updateSmartAlbum(id: number, body: SmartAlbumInput) {
    return this.write(this.http.patch<ApiResponse<SmartAlbum>>(`${API}/api/smart-albums/${id}`, body));
  }
  deleteSmartAlbum(id: number) { return this.write(this.http.delete<ApiResponse<unknown>>(`${API}/api/smart-albums/${id}`)); }
  deleteAlbum(id: number) { return this.write(this.http.delete<ApiResponse<{ removed_memberships: number }>>(`${API}/api/albums/${id}`)); }

  users(params?: Record<string, string>) { return this.read<ApiResponse<UserSummary[]>>('/api/users', params); }
  userProfile(username: string) { return this.read<ApiResponse<UserProfile>>(`/api/users/${encodeURIComponent(username)}`); }
  userAlbums(username: string, params?: Record<string, string>) { return this.read<ApiResponse<PublicAlbum[]>>(`/api/users/${encodeURIComponent(username)}/albums`, params); }
  avatarUrl(userId: number) { return `${API}/api/users/${userId}/avatar`; }

  media(albumId: number, params?: Record<string, string>) { return this.read<ApiResponse<Media[]>>(`/api/albums/${albumId}/media`, params); }
  /** Con `albumId`, la vista desde ese álbum: el servidor exige pertenencia y
   * devuelve el rol de ESE álbum. `albums` (sus álbumes) solo llega al dueño. */
  mediaDetail(id: number, albumId?: number | null) {
    return this.read<ApiResponse<MediaDetailData>>(`/api/media/${id}`, albumId ? { album_id: String(albumId) } : undefined);
  }
  /** Pertenencia a un álbum (L10B, solo el dueño). Solo relacional: nunca
   * copia ni borra el archivo, y quitar del último álbum lo deja en la biblioteca. */
  addToAlbum(albumId: number, mediaId: number) {
    return this.write(this.http.put<ApiResponse<{ album_id: number; asset_id: number; added: boolean }>>(`${API}/api/albums/${albumId}/assets/${mediaId}`, {}));
  }
  removeFromAlbum(albumId: number, mediaId: number) {
    return this.write(this.http.delete<ApiResponse<{ album_id: number; asset_id: number; remaining_albums: number }>>(`${API}/api/albums/${albumId}/assets/${mediaId}`));
  }
  createMedia(albumId: number, payload: MediaUploadPayload) { return this.write(this.http.post<ApiResponse<Media>>(`${API}/api/albums/${albumId}/media`, this.uploadForm(payload))); }
  createMediaWithProgress(albumId: number, payload: MediaUploadPayload): Observable<HttpEvent<ApiResponse<Media>>> {
    return this.http.post<ApiResponse<Media>>(
      `${API}/api/albums/${albumId}/media`,
      this.uploadForm(payload),
      { observe: 'events', reportProgress: true },
    ).pipe(tap(event => {
      // Los eventos de bytes no cambian lecturas. Solo la respuesta confirma
      // que el backend insertó el media y hace falta invalidar listados.
      if (event.type === HttpEventType.Response) this.invalidate();
    }));
  }
  deleteMedia(id: number) { return this.write(this.http.delete<ApiResponse<unknown>>(`${API}/api/media/${id}`)); }
  favorite(id: number, is_favorite: boolean) { return this.write(this.http.patch<ApiResponse<Media>>(`${API}/api/media/${id}/favorite`, { is_favorite })); }
  archive(id: number, archived: boolean) { return this.write(this.http.patch<ApiResponse<{ id: number; album_id: number | null; archived_at: string | null }>>(`${API}/api/media/${id}/archive`, { archived })); }
  updateMedia(id: number, payload: MediaUpdatePayload) { return this.write(this.http.patch<ApiResponse<Media>>(`${API}/api/media/${id}`, payload)); }
  mediaContext(id: number) { return this.read<ApiResponse<{ context: MediaContext | null }>>(`/api/media/${id}/context`); }
  places() { return this.read<ApiResponse<Place[]>>('/api/places'); }
  placeMedia(params: Record<string, string>) { return this.read<ApiResponse<Media[]>>('/api/places/media', params); }
  /** Completa de una vez todo el contexto que se pueda: lugar, sol, festivo y clima. */
  enrichContext(id: number, refresh = false) {
    return this.write(this.http.post<ApiResponse<{ context: MediaContext | null; results: ContextResults }>>(`${API}/api/media/${id}/context/enrich`, { refresh }));
  }
  /** Sugerencias efímeras: el servidor no guarda nada, las confirma el usuario
   * con `createTag`/`assignTags` de siempre. */
  suggestTags(id: number) { return this.write(this.http.post<ApiResponse<{ suggestions: TagSuggestion[] }>>(`${API}/api/media/${id}/tag-suggestions`, {})); }
  mediaOcr(id: number) { return this.read<ApiResponse<{ ocr: MediaOcr | null }>>(`/api/media/${id}/ocr`); }
  detectOcr(id: number) { return this.write(this.http.post<ApiResponse<{ ocr: MediaOcr | null }>>(`${API}/api/media/${id}/ocr`, {})); }
  deleteOcr(id: number) { return this.write(this.http.delete<ApiResponse<unknown>>(`${API}/api/media/${id}/ocr`)); }
  /** Fotos de toda la biblioteca por título, descripción o texto detectado. */
  searchMedia(params: Record<string, string>) { return this.read<ApiResponse<Media[]>>('/api/media/search', params); }
  /** `archived` lee la pagina Archivo: la misma cronologia, solo lo archivado. */
  library(cursor: string | null, limit = 60, archived = false) {
    const params: Record<string, string> = { limit: String(limit) };
    if (cursor) params['cursor'] = cursor;
    if (archived) params['archived'] = 'only';
    return this.read<LibraryResponse>('/api/media/library', params);
  }
  /** «Añadidos recientemente» de Biblioteca: mismo alcance que la cronología. */
  libraryRecent() { return this.read<ApiResponse<LibraryMedia[]>>('/api/media/library/recent'); }
  favorites() { return this.read<ApiResponse<Media[]>>('/api/media/favorites'); }
  trash() { return this.read<ApiResponse<Media[]>>('/api/trash'); }
  restore(id: number) { return this.write(this.http.post<ApiResponse<unknown>>(`${API}/api/media/${id}/restore`, {})); }
  deleteMediaPermanent(id: number) { return this.write(this.http.delete<ApiResponse<unknown>>(`${API}/api/media/${id}/permanent`)); }
  restoreTrashAll() { return this.write(this.http.post<ApiResponse<{ restored_count: number }>>(`${API}/api/trash/restore-all`, {})); }
  deleteTrashAll() { return this.write(this.http.delete<ApiResponse<{ deleted_count: number; removed_files: number }>>(`${API}/api/trash`)); }

  // Los originales no se memorizan: pesan megas y guardarlos retendría cada
  // foto abierta en memoria hasta recargar. Las previews sí, acotadas (arriba).
  mediaFile(id: number) { return this.http.get(`${API}/api/media/${id}/file`, { responseType: 'blob' }); }
  /**
   * Versión reducida (WebP ≤1280px) para cuadrículas y portadas. El servidor
   * cae al original cuando esa foto no tiene vista previa (un video, o algo
   * subido antes de la fase 13), así que quien pinta una miniatura puede
   * pedirla siempre sin comprobar nada.
   */
  mediaPreview(id: number): Observable<Blob> {
    const kept = this.previews.get(id);
    if (kept) {
      this.previews.delete(id);
      this.previews.set(id, kept);
      return of(kept);
    }
    return this.http.get(`${API}/api/media/${id}/preview`, { responseType: 'blob' }).pipe(
      tap(blob => this.keepPreview(id, blob)),
    );
  }
  /** Imagen del selector de pin manual. Como el resto de blobs protegidos, sin cache. */
  staticMap(params: Record<string, string>) { return this.http.get(`${API}/api/location/staticmap`, { params, responseType: 'blob' }); }
  locationSearch(q: string, country?: string) { return this.read<ApiResponse<{ display_name: string; latitude: number; longitude: number }[]>>('/api/location/search', country ? { q, country } : { q }); }
  sharedMediaFile(token: string, id: number) { return this.http.get(`${API}/api/shared/${token}/media/${id}/file`, { responseType: 'blob' }); }
  sharedMediaPreview(token: string, id: number) { return this.http.get(`${API}/api/shared/${token}/media/${id}/preview`, { responseType: 'blob' }); }

  /** Sin `albumId` devuelve el vocabulario propio. Con él, el del dueño de ese
   * álbum: un colaborador debe etiquetar con las etiquetas del dueño, no con
   * las suyas, o el backend rechaza la asignación. */
  tags(q = '', albumId?: number, paging?: { page: number; perPage: number }) {
    const params: Record<string, string> = {};
    if (q) params['q'] = q;
    if (albumId) params['album_id'] = String(albumId);
    if (paging) {
      params['page'] = String(paging.page);
      params['per_page'] = String(paging.perPage);
    }
    return this.read<ApiResponse<Tag[]>>('/api/tags', Object.keys(params).length ? params : undefined);
  }
  createTag(name: string, albumId?: number) { return this.write(this.http.post<ApiResponse<Tag>>(`${API}/api/tags`, albumId ? { name, album_id: albumId } : { name })); }
  assignTags(mediaId: number, tag_ids: number[]) { return this.write(this.http.post<ApiResponse<unknown>>(`${API}/api/media/${mediaId}/tags`, { tag_ids })); }
  unassignTag(mediaId: number, tagId: number) { return this.write(this.http.delete<ApiResponse<unknown>>(`${API}/api/media/${mediaId}/tags/${tagId}`)); }

  shares(albumId: number) { return this.read<ApiResponse<Share[]>>(`/api/albums/${albumId}/shares`); }
  createShare(albumId: number, body: {
    permission: SharePermission; capabilities?: AlbumCapability[]; share_type?: 'account' | 'public_link';
    shared_with_user_id?: number; create_link?: boolean; expires_at?: string | null;
    password?: string | null; allow_original_download?: boolean; show_metadata?: boolean;
  }) {
    return this.write(this.http.post<ApiResponse<Share>>(`${API}/api/albums/${albumId}/shares`, body));
  }
  updateSharePermission(shareId: number, permission: SharePermission, capabilities: AlbumCapability[], publicLinkSettings?: {
    password?: string | null; allow_original_download?: boolean; show_metadata?: boolean;
  }) {
    return this.write(this.http.patch<ApiResponse<Share>>(`${API}/api/shares/${shareId}`, { permission, capabilities, ...publicLinkSettings }));
  }
  claimShare(token: string) {
    return this.write(this.http.post<ApiResponse<{ share_id: number; album_id: number; permission: SharePermission; capabilities: AlbumCapability[] }>>(`${API}/api/shared/${token}/claim`, {}));
  }
  disableShare(id: number) { return this.write(this.http.delete<ApiResponse<unknown>>(`${API}/api/shares/${id}`)); }
  /** Descifra y devuelve el token de un enlace YA CREADO -- no cambia nada.
   * Camino normal para "cópiamelo otra vez" o "enséñame el QR otra vez". */
  revealShareLink(id: number) {
    return this.write(this.http.post<ApiResponse<{ id: number; share_type: 'account' | 'public_link'; token: string }>>(`${API}/api/shares/${id}/reveal`, {}));
  }
  /** Emite un token NUEVO e invalida el anterior -- para cuando el enlace se
   * filtró y hay que matarlo sin dejar de compartir el álbum. Con `reveal`
   * disponible, esto deja de ser el único camino para volver a copiarlo. */
  regenerateShareLink(id: number) {
    return this.write(this.http.post<ApiResponse<{ id: number; share_type: 'account' | 'public_link'; token: string }>>(`${API}/api/shares/${id}/regenerate`, {}));
  }
  sharedLink(token: string) { return this.read<ApiResponse<{ share: Share; album: Album; media: Media[] }>>(`/api/shared/${token}/media`); }
  /** El MISMO detalle que ve una cuenta con acceso de solo lectura, para que
   * el visor público reutilice `MediaDetail` en vez de duplicarlo. */
  sharedMediaDetail(token: string, id: number) {
    return this.read<ApiResponse<SharedMediaDetail>>(`/api/shared/${token}/media/${id}`);
  }
  /** No pasa por `write()`: no cambia ningún dato que otra lectura tenga
   * en caché, solo deja una cookie de desbloqueo para ESTE enlace. */
  unlockSharedLink(token: string, password: string) {
    return this.http.post<ApiResponse<unknown>>(`${API}/api/shared/${token}/unlock`, { password });
  }
}
