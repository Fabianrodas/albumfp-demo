import { describe, expect, it } from 'vitest';
import { convertToParamMap } from '@angular/router';
import {
  AdvancedSearchValue,
  EMPTY_ADVANCED_SEARCH,
  activeAdvancedSearchFilters,
  advancedSearchFromParams,
  advancedSearchToParams,
  searchFromSmartFilters,
  smartFilterSummary,
  smartFiltersFromSearch,
} from './advanced-search';


const EMPTY: AdvancedSearchValue = {
  q: '', mediaType: '', dateFrom: '', dateTo: '', year: '', albumId: '',
  favorite: '', tagId: '', place: '', sort: 'relevance', direction: 'desc', archived: '',
};


describe('advanced search URL state', () => {
  it('serializes only allowlisted non-empty values', () => {
    expect(advancedSearchToParams({
      ...EMPTY,
      q: '  Samborondón  ',
      mediaType: 'image',
      favorite: 'true',
      albumId: '12',
    })).toEqual({
      q: 'Samborondón', media_type: 'image', favorite: 'true', album_id: '12',
    });
  });

  it('round-trips the archive filter and drops unknown archive values', () => {
    const value = { ...EMPTY, q: 'playa', archived: 'only' as const };
    expect(advancedSearchToParams(value)).toEqual({ q: 'playa', archived: 'only' });
    expect(advancedSearchFromParams(convertToParamMap({ archived: 'exclude' })).archived).toBe('exclude');
    expect(advancedSearchFromParams(convertToParamMap({ archived: 'all' })).archived).toBe('');
    expect(activeAdvancedSearchFilters({ ...EMPTY, archived: 'only' })).toEqual(['archived']);
  });

  it('ignores unknown or invalid values read from the URL', () => {
    const value = advancedSearchFromParams(convertToParamMap({
      q: 'familia', media_type: 'audio', favorite: 'yes', sort: 'DROP TABLE',
      direction: 'sideways', year: '9999', date_to: '9999-12-31', secret: 'never-forward-this',
    }));
    expect(value).toEqual({ ...EMPTY, q: 'familia' });
  });

  it('reports structured filters separately from the text query and sorting', () => {
    expect(activeAdvancedSearchFilters({
      ...EMPTY, q: 'playa', year: '2024', place: 'Guayas', favorite: 'false',
    })).toEqual(['year', 'favorite', 'place']);
  });

  it('does not serialize an inapplicable direction for relevance', () => {
    expect(advancedSearchToParams({ ...EMPTY, q: 'playa', direction: 'asc' })).toEqual({ q: 'playa' });
  });

  it('normalizes relevance URLs to descending ranking', () => {
    expect(advancedSearchFromParams(convertToParamMap({ q: 'playa', direction: 'asc' })).direction).toBe('desc');
  });

  it('does not serialize the date_to value that would overflow the backend', () => {
    expect(advancedSearchToParams({ ...EMPTY, q: 'playa', dateTo: '9999-12-31' })).toEqual({ q: 'playa' });
  });
});

describe('smart album definitions (L11)', () => {
  const base = { ...EMPTY_ADVANCED_SEARCH };

  it('turns the search form into the typed definition the API stores, without sort or paging', () => {
    expect(smartFiltersFromSearch({
      ...base, q: '  playa ', year: '2026', favorite: 'true', tagId: '3', archived: 'exclude',
      mediaType: 'image', sort: 'title', direction: 'asc',
    })).toEqual({ q: 'playa', year: 2026, favorite: true, tag_id: 3, archived: 'exclude', media_type: 'image' });
  });

  it('keeps favorite=false as a real criterion and drops empty or invalid values', () => {
    expect(smartFiltersFromSearch({ ...base, favorite: 'false' })).toEqual({ favorite: false });
    expect(smartFiltersFromSearch({ ...base, dateFrom: '2026-13', year: '12', albumId: '0' })).toEqual({});
    expect(smartFiltersFromSearch(base)).toEqual({});
  });

  it('loads a stored definition back into the same controls without drift', () => {
    const stored = {
      q: 'rio', media_type: 'video' as const, date_from: '2026-01-01', date_to: '2026-02-01', year: 2026,
      album_id: 4, favorite: false, tag_id: 9, place: 'Quito', archived: 'only' as const,
    };
    const value = searchFromSmartFilters(stored);
    expect(value).toEqual({
      q: 'rio', mediaType: 'video', dateFrom: '2026-01-01', dateTo: '2026-02-01', year: '2026', albumId: '4',
      favorite: 'false', tagId: '9', place: 'Quito', archived: 'only', sort: 'relevance', direction: 'desc',
    });
    expect(smartFiltersFromSearch(value)).toEqual(stored);
  });

  it('summarizes a definition in words, resolving names without inventing stale ones', () => {
    const references = { album: { id: 4, titulo: 'Vacaciones' }, tag: { id: 9, name: 'Viaje' } };
    expect(smartFilterSummary({ year: 2026, favorite: true, tag_id: 9, album_id: 4, media_type: 'image', archived: 'only' }, references))
      .toEqual(['Tipo: Fotos', 'Año: 2026', 'Álbum: Vacaciones', 'Favoritos', 'Etiqueta: Viaje', 'Archivado: Solo archivados']);
    expect(smartFilterSummary({ album_id: 4, tag_id: 9 }, { album: null, tag: null }))
      .toEqual(['Álbum no disponible', 'Etiqueta no disponible']);
    expect(smartFilterSummary({ q: 'playa', favorite: false, archived: 'exclude', place: 'Quito', date_from: '2026-01-01' },
      { album: null, tag: null }))
      .toEqual(['Texto: playa', 'Desde: 2026-01-01', 'No favoritos', 'Lugar: Quito', 'Archivado: Sin archivados']);
  });
});
