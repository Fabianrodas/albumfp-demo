import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { SmartAlbum } from '../../../core/services/album-api';
import { EMPTY_ADVANCED_SEARCH } from '../../../core/utils/advanced-search';
import { SmartAlbumEditor } from './smart-album-editor';

const page = <T>(data: T[]) => ({ data, message: 'ok', meta: { pagination: { page: 1, per_page: 100, total: data.length, total_pages: 1 } } });

function setup() {
  TestBed.configureTestingModule({
    imports: [SmartAlbumEditor],
    providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
  });
  const http = TestBed.inject(HttpTestingController);
  const fixture = TestBed.createComponent(SmartAlbumEditor);
  return { http, fixture, editor: fixture.componentInstance };
}

/** El selector de álbumes sale SOLO de /api/albums: un álbum inteligente nunca es opción. */
function flushOptions(http: HttpTestingController) {
  http.match(r => r.url === '/api/albums').forEach(r => r.flush(page([
    { id: 4, user_id: 1, titulo: 'Vacaciones', is_private: true, role: 'owner', created_at: '2026-01-01' },
  ])));
  http.match(r => r.url === '/api/tags').forEach(r => r.flush(page([{ id: 9, name: 'viaje' }])));
  http.expectNone(r => r.url.startsWith('/api/smart-albums'));
}

describe('SmartAlbumEditor', () => {
  it('creates a smart album with the typed definition from the shared search controls', async () => {
    const { http, fixture, editor } = setup();
    fixture.componentRef.setInput('prefill', { ...EMPTY_ADVANCED_SEARCH, year: '2026', favorite: 'true', sort: 'title' });
    fixture.componentRef.setInput('open', true);
    fixture.detectChanges();
    flushOptions(http);
    await fixture.whenStable();
    fixture.detectChanges();
    const saved = vi.fn();
    editor.saved.subscribe(saved);

    editor.title = 'Favoritos 2026';
    editor.save();

    const request = http.expectOne(r => r.url === '/api/smart-albums' && r.method === 'POST');
    expect(request.request.body).toEqual({ titulo: 'Favoritos 2026', descripcion: null, filters: { year: 2026, favorite: true } });
    request.flush({ data: { id: 3, titulo: 'Favoritos 2026', filters: { year: 2026, favorite: true } }, message: 'ok' });
    expect(saved).toHaveBeenCalledWith(expect.objectContaining({ id: 3 }));
    // Las opciones del selector de álbum son de los álbumes normales.
    expect(fixture.nativeElement.textContent).toContain('Vacaciones');
  });

  it('asks for a name and for at least one filter before calling the API', () => {
    const { http, fixture, editor } = setup();
    fixture.componentRef.setInput('open', true);
    fixture.detectChanges();
    flushOptions(http);

    editor.title = '';
    editor.form.controls.year.setValue('2026');
    editor.save();
    expect(editor.error()).toContain('nombre');

    editor.title = 'Sin filtros';
    editor.form.controls.year.setValue('');
    editor.save();
    expect(editor.error()).toContain('al menos un filtro');

    http.expectNone(r => r.url === '/api/smart-albums');
  });

  it('loads a stored definition into the same controls and saves it back without drift', async () => {
    const { http, fixture, editor } = setup();
    const stored: SmartAlbum = {
      id: 5, titulo: 'Viajes', descripcion: 'Todo', created_at: '2026-09-23', updated_at: null,
      filters: { q: 'rio', album_id: 4, tag_id: 9, favorite: false, archived: 'exclude', date_from: '2026-01-01' },
      references: { album: { id: 4, titulo: 'Vacaciones' }, tag: { id: 9, name: 'viaje' } }, stale_references: [],
    };
    fixture.componentRef.setInput('smart', stored);
    fixture.componentRef.setInput('open', true);
    fixture.detectChanges();
    flushOptions(http);
    await fixture.whenStable();

    expect(editor.title).toBe('Viajes');
    expect(editor.form.getRawValue()).toEqual(expect.objectContaining({ q: 'rio', albumId: '4', tagId: '9', favorite: 'false', archived: 'exclude' }));

    editor.save();
    const request = http.expectOne(r => r.url === '/api/smart-albums/5' && r.method === 'PATCH');
    expect(request.request.body).toEqual({ titulo: 'Viajes', descripcion: 'Todo', filters: stored.filters });
  });
});
