import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { Albums } from './albums';

const page = <T>(data: T[]) => ({ data, message: 'ok', meta: { pagination: { page: 1, per_page: 60, total: data.length, total_pages: 1 } } });
const SMART = {
  id: 5, titulo: 'Favoritos 2026', descripcion: null, created_at: '2026-09-23', updated_at: null,
  filters: { year: 2026, favorite: true }, references: { album: null, tag: null }, stale_references: [],
};

async function setup(query: Record<string, string>) {
  TestBed.configureTestingModule({
    imports: [Albums],
    providers: [
      provideHttpClient(), provideHttpClientTesting(), provideRouter([]),
      { provide: ActivatedRoute, useValue: { queryParamMap: of(convertToParamMap(query)), snapshot: { queryParamMap: convertToParamMap(query) } } },
    ],
  });
  const http = TestBed.inject(HttpTestingController);
  const fixture = TestBed.createComponent(Albums);
  fixture.detectChanges();
  http.match(r => r.url === '/api/albums').forEach(r => r.flush(page([
    { id: 4, user_id: 1, titulo: 'Vacaciones', is_private: true, role: 'owner', created_at: '2026-01-01' },
  ])));
  http.match(r => r.url === '/api/tags').forEach(r => r.flush(page([])));
  http.match(r => r.url === '/api/media/search').forEach(r => r.flush(page([])));
  http.match(r => r.url === '/api/smart-albums').forEach(r => r.flush(page([SMART])));
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
  return { http, fixture, albums: fixture.componentInstance, el: fixture.nativeElement as HTMLElement };
}

describe('Albums page smart albums (L11)', () => {
  it('lists smart albums in their own section next to the regular albums', async () => {
    const { el } = await setup({});

    const section = el.querySelector('.smart-section');
    expect(section?.textContent).toContain('Álbumes inteligentes');
    expect(section?.querySelector('app-smart-album-card')?.textContent).toContain('Favoritos 2026');
    expect(el.querySelector('app-album-card')?.textContent).toContain('Vacaciones');
    expect([...el.querySelectorAll('button')].some(b => b.textContent?.includes('Álbum inteligente'))).toBe(true);
  });

  it('offers to save the current search as a smart album, prefilled from the active filters', async () => {
    const { el, albums, fixture } = await setup({ year: '2026', favorite: 'true' });

    expect(el.querySelector('.smart-section')).toBeNull();
    const save = [...el.querySelectorAll('button')].find(b => b.textContent?.includes('Guardar búsqueda como álbum inteligente'));
    expect(save).toBeTruthy();
    save!.click();
    fixture.detectChanges();

    expect(albums.smartEditorOpen()).toBe(true);
    expect(albums.smartPrefill()).toEqual(expect.objectContaining({ year: '2026', favorite: 'true' }));
  });
});
