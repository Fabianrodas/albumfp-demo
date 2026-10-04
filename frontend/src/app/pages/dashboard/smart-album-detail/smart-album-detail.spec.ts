import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ActivatedRoute, convertToParamMap, provideRouter, Router } from '@angular/router';
import { of } from 'rxjs';
import { Confirm } from '../../../core/services/confirm';
import { SmartAlbumDetail } from './smart-album-detail';

const DETAIL = {
  id: 5, titulo: 'Favoritos 2026', descripcion: 'Lo mejor del año', created_at: '2026-09-23', updated_at: null,
  filters: { year: 2026, favorite: true }, references: { album: null, tag: null }, stale_references: [],
};
const pagination = (total: number) => ({ meta: { pagination: { page: 1, per_page: 60, total, total_pages: Math.ceil(total / 60) } } });

function setup() {
  const params = convertToParamMap({ id: '5' });
  TestBed.configureTestingModule({
    imports: [SmartAlbumDetail],
    providers: [
      provideHttpClient(), provideHttpClientTesting(), provideRouter([]),
      { provide: ActivatedRoute, useValue: { snapshot: { paramMap: params }, paramMap: of(params) } },
    ],
  });
  const http = TestBed.inject(HttpTestingController);
  const fixture = TestBed.createComponent(SmartAlbumDetail);
  fixture.detectChanges();
  return { http, fixture, page: fixture.componentInstance, el: fixture.nativeElement as HTMLElement };
}

async function load(fixture: ReturnType<typeof setup>['fixture'], http: HttpTestingController, media: object, total = 1) {
  http.expectOne('/api/smart-albums/5').flush({ data: DETAIL, message: 'ok' });
  http.expectOne(r => r.url === '/api/smart-albums/5/media').flush({ data: media, message: 'ok', ...pagination(total) });
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
}

describe('SmartAlbumDetail', () => {
  it('shows the definition, its badge and summary, and the dynamic results', async () => {
    const { http, fixture, el } = setup();
    await load(fixture, http, [{ id: 21, album_id: null, file_type: 'image', title: 'Suelta' }]);

    expect(el.querySelector('h1')?.textContent).toContain('Favoritos 2026');
    expect(el.textContent).toContain('Inteligente');
    expect(el.textContent).toContain('Lo mejor del año');
    expect(el.textContent).toContain('Año: 2026');
    expect(el.textContent).toContain('Favoritos');
    expect(el.textContent).toContain('Suelta');
    expect(el.querySelector('app-media-grid')).not.toBeNull();
  });

  it('is not a physical album: no upload, share, privacy, cover or membership controls', async () => {
    const { http, fixture, el } = setup();
    await load(fixture, http, [{ id: 21, album_id: 4, file_type: 'image', title: 'Foto' }]);

    const text = el.textContent || '';
    for (const forbidden of ['Agregar', 'Compartir', 'Privado', 'Público', 'Portada', 'Quitar del álbum', 'Añadir a álbum', 'Actividad']) {
      expect(text).not.toContain(forbidden);
    }
    expect(el.querySelector('app-upload-panel')).toBeNull();
    // L12: la actividad es de los álbumes normales; uno inteligente no la pide ni la pinta.
    expect(el.querySelector('app-album-activity')).toBeNull();
    http.expectNone(r => r.url.includes('/activity'));
    expect(el.querySelectorAll('.media-card__actions button').length).toBe(0);
  });

  it('opens a result as the asset itself and remembers how to come back', async () => {
    const { http, fixture, el } = setup();
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    await load(fixture, http, [{ id: 21, album_id: 4, file_type: 'image', title: 'Foto' }]);

    (el.querySelector('button.media-card__asset') as HTMLButtonElement).click();

    expect(navigate).toHaveBeenCalledWith(['/recuerdos', 21], { queryParams: { from: 'smart', smart_album_id: '5' } });
  });

  it('fails closed with a recoverable state when a saved filter references something that no longer exists', async () => {
    const { http, fixture, el, page } = setup();
    http.expectOne('/api/smart-albums/5').flush({ data: { ...DETAIL, filters: { tag_id: 9 }, stale_references: ['tag_id'] }, message: 'ok' });
    http.expectOne(r => r.url === '/api/smart-albums/5/media').flush(
      { ok: false, code: 'smart_album_stale_reference', message: 'Este álbum inteligente usa un filtro que ya no existe. Edita sus filtros para continuar.' },
      { status: 409, statusText: 'Conflict' },
    );
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(el.textContent).toContain('Este álbum inteligente usa un filtro que ya no existe.');
    expect(el.querySelector('app-media-grid')).toBeNull();
    const edit = [...el.querySelectorAll('button')].find(b => b.textContent?.includes('Editar filtros'));
    expect(edit).toBeTruthy();
    edit!.click();
    expect(page.editing()).toBe(true);
  });

  it('deletes only the definition after a confirmation that says photos stay untouched', async () => {
    const { http, fixture, page } = setup();
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigateByUrl').mockResolvedValue(true);
    const ask = vi.spyOn(TestBed.inject(Confirm), 'ask').mockResolvedValue(true);
    await load(fixture, http, []);

    await page.remove();

    expect(ask.mock.calls[0][0].message).toContain('no se borran');
    http.expectOne(r => r.url === '/api/smart-albums/5' && r.method === 'DELETE').flush({ message: 'ok' });
    expect(navigate).toHaveBeenCalledWith('/albumes');
  });

  it('pages through large result sets with a manual load-more', async () => {
    const { http, fixture, el } = setup();
    await load(fixture, http, [{ id: 1, album_id: null, file_type: 'image', title: 'Uno' }], 61);

    expect(el.textContent).toContain('Cargar más');
    expect(el.textContent).toContain('61');
  });
});
