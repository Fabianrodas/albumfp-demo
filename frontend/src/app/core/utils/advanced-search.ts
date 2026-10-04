import { FormControl, FormGroup } from '@angular/forms';
import { ParamMap, Params } from '@angular/router';
import { SmartAlbumFilters, SmartAlbumReferences } from '../services/album-api';


export type SearchMediaType = '' | 'image' | 'video';
export type SearchFavorite = '' | 'true' | 'false';
export type SearchSort = 'relevance' | 'taken_at' | 'created_at' | 'title';
export type SearchDirection = 'asc' | 'desc';
export type SearchArchived = '' | 'only' | 'exclude';

export interface AdvancedSearchValue {
  q: string;
  mediaType: SearchMediaType;
  dateFrom: string;
  dateTo: string;
  year: string;
  albumId: string;
  favorite: SearchFavorite;
  tagId: string;
  place: string;
  sort: SearchSort;
  direction: SearchDirection;
  archived: SearchArchived;
}

export type StructuredSearchFilter =
  'mediaType' | 'dateFrom' | 'dateTo' | 'year' | 'albumId' | 'favorite' | 'tagId' | 'place' | 'archived';

export const EMPTY_ADVANCED_SEARCH: AdvancedSearchValue = {
  q: '', mediaType: '', dateFrom: '', dateTo: '', year: '', albumId: '',
  favorite: '', tagId: '', place: '', sort: 'relevance', direction: 'desc', archived: '',
};

const MEDIA_TYPES = new Set<SearchMediaType>(['', 'image', 'video']);
const FAVORITES = new Set<SearchFavorite>(['', 'true', 'false']);
const SORTS = new Set<SearchSort>(['relevance', 'taken_at', 'created_at', 'title']);
const DIRECTIONS = new Set<SearchDirection>(['asc', 'desc']);
const ARCHIVED = new Set<SearchArchived>(['', 'only', 'exclude']);

function positiveId(value: string | null) {
  return value && /^\d+$/.test(value) && Number(value) > 0 ? value : '';
}

function isoDate(value: string | null) {
  return value && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : '';
}

function endDate(value: string | null) {
  const parsed = isoDate(value);
  return parsed && parsed <= '9998-12-31' ? parsed : '';
}

function year(value: string | null) {
  return value && /^\d{4}$/.test(value) && Number(value) >= 1 && Number(value) <= 9998 ? value : '';
}

function oneOf<T extends string>(value: string | null, allowed: Set<T>, fallback: T): T {
  return allowed.has(value as T) ? value as T : fallback;
}

export function advancedSearchFromParams(params: ParamMap): AdvancedSearchValue {
  const sort = oneOf(params.get('sort'), SORTS, 'relevance');
  return {
    q: (params.get('q') || '').slice(0, 200),
    mediaType: oneOf(params.get('media_type'), MEDIA_TYPES, ''),
    dateFrom: isoDate(params.get('date_from')),
    dateTo: endDate(params.get('date_to')),
    year: year(params.get('year')),
    albumId: positiveId(params.get('album_id')),
    favorite: oneOf(params.get('favorite'), FAVORITES, ''),
    tagId: positiveId(params.get('tag_id')),
    place: (params.get('place') || '').slice(0, 120),
    sort,
    direction: sort === 'relevance' ? 'desc' : oneOf(params.get('direction'), DIRECTIONS, 'desc'),
    archived: oneOf(params.get('archived'), ARCHIVED, ''),
  };
}

export function advancedSearchToParams(value: AdvancedSearchValue): Params {
  const params: Params = {};
  const q = value.q.trim().slice(0, 200);
  const place = value.place.trim().slice(0, 120);
  if (q) params['q'] = q;
  if (MEDIA_TYPES.has(value.mediaType) && value.mediaType) params['media_type'] = value.mediaType;
  if (isoDate(value.dateFrom)) params['date_from'] = value.dateFrom;
  if (endDate(value.dateTo)) params['date_to'] = value.dateTo;
  if (year(value.year)) params['year'] = value.year;
  if (positiveId(value.albumId)) params['album_id'] = value.albumId;
  if (FAVORITES.has(value.favorite) && value.favorite) params['favorite'] = value.favorite;
  if (positiveId(value.tagId)) params['tag_id'] = value.tagId;
  if (place) params['place'] = place;
  if (ARCHIVED.has(value.archived) && value.archived) params['archived'] = value.archived;
  if (SORTS.has(value.sort) && value.sort !== 'relevance') params['sort'] = value.sort;
  if (value.sort !== 'relevance' && DIRECTIONS.has(value.direction) && value.direction !== 'desc') {
    params['direction'] = value.direction;
  }
  return params;
}

export function activeAdvancedSearchFilters(value: AdvancedSearchValue): StructuredSearchFilter[] {
  const active: StructuredSearchFilter[] = [];
  if (value.mediaType) active.push('mediaType');
  if (value.dateFrom) active.push('dateFrom');
  if (value.dateTo) active.push('dateTo');
  if (value.year) active.push('year');
  if (value.albumId) active.push('albumId');
  if (value.favorite) active.push('favorite');
  if (value.tagId) active.push('tagId');
  if (value.place.trim()) active.push('place');
  if (value.archived) active.push('archived');
  return active;
}

/** El formulario de la búsqueda avanzada. Lo usan la búsqueda de /albumes y el
 * editor de álbumes inteligentes, para que los dos hablen el mismo vocabulario. */
export function createAdvancedSearchForm(value: AdvancedSearchValue = EMPTY_ADVANCED_SEARCH) {
  return new FormGroup({
    q: new FormControl(value.q, { nonNullable: true }),
    mediaType: new FormControl<SearchMediaType>(value.mediaType, { nonNullable: true }),
    dateFrom: new FormControl(value.dateFrom, { nonNullable: true }),
    dateTo: new FormControl(value.dateTo, { nonNullable: true }),
    year: new FormControl(value.year, { nonNullable: true }),
    albumId: new FormControl(value.albumId, { nonNullable: true }),
    favorite: new FormControl<SearchFavorite>(value.favorite, { nonNullable: true }),
    tagId: new FormControl(value.tagId, { nonNullable: true }),
    place: new FormControl(value.place, { nonNullable: true }),
    sort: new FormControl<SearchSort>(value.sort, { nonNullable: true }),
    direction: new FormControl<SearchDirection>(value.direction, { nonNullable: true }),
    archived: new FormControl<SearchArchived>(value.archived, { nonNullable: true }),
  });
}

export type AdvancedSearchForm = ReturnType<typeof createAdvancedSearchForm>;

/** L11: los criterios del formulario como la definición tipada que guarda el
 * servidor. Pasa por los mismos saneadores que la URL; orden y dirección no
 * se guardan porque no son parte de la identidad del álbum inteligente. */
export function smartFiltersFromSearch(value: AdvancedSearchValue): SmartAlbumFilters {
  const params = advancedSearchToParams(value);
  const filters: SmartAlbumFilters = {};
  if (params['q']) filters.q = params['q'];
  if (params['media_type']) filters.media_type = params['media_type'];
  if (params['date_from']) filters.date_from = params['date_from'];
  if (params['date_to']) filters.date_to = params['date_to'];
  if (params['year']) filters.year = Number(params['year']);
  if (params['album_id']) filters.album_id = Number(params['album_id']);
  if (params['favorite']) filters.favorite = params['favorite'] === 'true';
  if (params['tag_id']) filters.tag_id = Number(params['tag_id']);
  if (params['place']) filters.place = params['place'];
  if (params['archived']) filters.archived = params['archived'];
  return filters;
}

/** Lo inverso: una definición guardada de vuelta en los mismos controles. */
export function searchFromSmartFilters(filters: SmartAlbumFilters): AdvancedSearchValue {
  return {
    ...EMPTY_ADVANCED_SEARCH,
    q: filters.q ?? '',
    mediaType: filters.media_type ?? '',
    dateFrom: filters.date_from ?? '',
    dateTo: filters.date_to ?? '',
    year: filters.year ? String(filters.year) : '',
    albumId: filters.album_id ? String(filters.album_id) : '',
    favorite: filters.favorite === undefined ? '' : filters.favorite ? 'true' : 'false',
    tagId: filters.tag_id ? String(filters.tag_id) : '',
    place: filters.place ?? '',
    archived: filters.archived ?? '',
  };
}

/** La definición en palabras. Un id guardado que ya no existe se dice tal
 * cual ("no disponible"), nunca con un nombre inventado. */
export function smartFilterSummary(filters: SmartAlbumFilters, references: SmartAlbumReferences): string[] {
  const parts: string[] = [];
  if (filters.q) parts.push(`Texto: ${filters.q}`);
  if (filters.media_type) parts.push(`Tipo: ${filters.media_type === 'image' ? 'Fotos' : 'Videos'}`);
  if (filters.date_from) parts.push(`Desde: ${filters.date_from}`);
  if (filters.date_to) parts.push(`Hasta: ${filters.date_to}`);
  if (filters.year) parts.push(`Año: ${filters.year}`);
  if (filters.album_id) parts.push(references.album ? `Álbum: ${references.album.titulo}` : 'Álbum no disponible');
  if (filters.favorite !== undefined) parts.push(filters.favorite ? 'Favoritos' : 'No favoritos');
  if (filters.tag_id) parts.push(references.tag ? `Etiqueta: ${references.tag.name}` : 'Etiqueta no disponible');
  if (filters.place) parts.push(`Lugar: ${filters.place}`);
  if (filters.archived) parts.push(`Archivado: ${filters.archived === 'only' ? 'Solo archivados' : 'Sin archivados'}`);
  return parts;
}
