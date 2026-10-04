import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ActivatedRoute, convertToParamMap, provideRouter, Router } from '@angular/router';
import { of } from 'rxjs';
import { ALBUM_CAPABILITIES } from '../../../core/models/album-permissions';
import { Confirm } from '../../../core/services/confirm';
import { MediaDetail } from './media-detail';

describe('MediaDetail edit dialog', () => {
  it('keeps location editing local and never requests an online map', async () => {
    const params = convertToParamMap({ albumId: '1', mediaId: '1' });
    TestBed.configureTestingModule({
      imports: [MediaDetail],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { snapshot: { paramMap: params, queryParamMap: convertToParamMap({}) }, paramMap: of(params) } },
      ],
    });
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(MediaDetail);
    fixture.detectChanges();

    http.expectOne('/api/media/1?album_id=1').flush({
      data: {
        media: { id: 1, album_id: 1, file_type: 'image', title: 'Foto', caption: null, taken_at: null, created_at: '2026-09-01T10:00:00', is_favorite: false },
        metadata: null, exif: null, tags: [], album_role: 'owner',
        album_capabilities: ALBUM_CAPABILITIES,
      },
      message: 'ok',
    });
    fixture.detectChanges();
    await fixture.whenStable();
    const staticMap = () => http.match(request => request.url === '/api/location/staticmap');

    expect(staticMap().length).toBe(0);

    fixture.componentInstance.openEdit();
    fixture.detectChanges();
    await fixture.whenStable();
    expect(staticMap().length).toBe(0);
    expect(fixture.nativeElement.textContent).toMatch(/mapas en .* desactivados/);
  });

  it('returns to the smart album it was opened from', () => {
    const params = convertToParamMap({ mediaId: '17' });
    const queryParamMap = convertToParamMap({ from: 'smart', smart_album_id: '7' });
    TestBed.configureTestingModule({
      imports: [MediaDetail],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { snapshot: { paramMap: params, queryParamMap, queryParams: {} }, paramMap: of(params) } },
      ],
    });
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    const fixture = TestBed.createComponent(MediaDetail);

    expect(fixture.componentInstance.backLabel).toBe('Volver al álbum inteligente');
    fixture.componentInstance.back();
    expect(navigate).toHaveBeenCalledWith(['/albumes/inteligentes', 7]);
  });

  it('falls back to the albums page when the smart album id is not valid', () => {
    const params = convertToParamMap({ mediaId: '17' });
    const queryParamMap = convertToParamMap({ from: 'smart', smart_album_id: 'x' });
    TestBed.configureTestingModule({
      imports: [MediaDetail],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { snapshot: { paramMap: params, queryParamMap, queryParams: {} }, paramMap: of(params) } },
      ],
    });
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    TestBed.createComponent(MediaDetail).componentInstance.back();
    expect(navigate).toHaveBeenCalledWith(['/albumes']);
  });

  it('returns to the focused library item when opened from the timeline', () => {
    const params = convertToParamMap({ albumId: '4', mediaId: '17' });
    const queryParamMap = convertToParamMap({ from: 'library', focus: '17' });
    TestBed.configureTestingModule({
      imports: [MediaDetail],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: { paramMap: params, queryParamMap, queryParams: { from: 'library', focus: '17' } },
            paramMap: of(params),
          },
        },
      ],
    });
    const router = TestBed.inject(Router);
    const navigate = vi.spyOn(router, 'navigate').mockResolvedValue(true);
    const fixture = TestBed.createComponent(MediaDetail);

    expect(fixture.componentInstance.backLabel).toBe('Volver a la biblioteca');
    fixture.componentInstance.back();
    expect(navigate).toHaveBeenCalledWith(['/biblioteca'], { queryParams: { focus: '17' } });
  });

  it('returns to the focused archive item when opened from the archive', () => {
    const params = convertToParamMap({ albumId: '4', mediaId: '31' });
    TestBed.configureTestingModule({
      imports: [MediaDetail],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: { paramMap: params, queryParamMap: convertToParamMap({ from: 'archive', focus: '31' }), queryParams: { from: 'archive', focus: '31' } },
            paramMap: of(params),
          },
        },
      ],
    });
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    const fixture = TestBed.createComponent(MediaDetail);

    expect(fixture.componentInstance.backLabel).toBe('Volver al archivo');
    fixture.componentInstance.back();
    expect(navigate).toHaveBeenCalledWith(['/archivo'], { queryParams: { focus: '31' } });
  });

  it('lets someone who can organize archive and unarchive from the detail', async () => {
    const params = convertToParamMap({ albumId: '1', mediaId: '1' });
    TestBed.configureTestingModule({
      imports: [MediaDetail],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { snapshot: { paramMap: params, queryParamMap: convertToParamMap({}) }, paramMap: of(params) } },
      ],
    });
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(MediaDetail);
    fixture.detectChanges();
    http.expectOne('/api/media/1?album_id=1').flush({
      data: {
        media: { id: 1, album_id: 1, file_type: 'video', title: 'Foto', caption: null, taken_at: null, created_at: '2026-09-01T10:00:00', is_favorite: false, archived_at: null },
        metadata: null, exif: null, tags: [], album_role: 'owner', album_capabilities: ALBUM_CAPABILITIES,
      },
      message: 'ok',
    });
    fixture.detectChanges();
    await fixture.whenStable();

    const archiveButton = () => ([...fixture.nativeElement.querySelectorAll('.detail-actions button')] as HTMLButtonElement[])
      .find(button => /rchivar/.test(button.textContent || ''));
    expect(archiveButton()?.textContent).toContain('Archivar');
    archiveButton()!.click();
    const patch = http.expectOne('/api/media/1/archive');
    expect(patch.request.body).toEqual({ archived: true });
    patch.flush({ data: { id: 1, album_id: 1, archived_at: '2026-09-19T10:00:00' }, message: 'ok' });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.querySelector('.archived-badge')?.textContent).toContain('Archivado');
    expect(archiveButton()?.textContent).toContain('Desarchivar');
  });

  it('returns to Inicio (where Explore lives since F01) when a public photo was opened from there', () => {
    const params = convertToParamMap({ albumId: '4', mediaId: '23' });
    TestBed.configureTestingModule({
      imports: [MediaDetail],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: { paramMap: params, queryParamMap: convertToParamMap({ from: 'explore' }), queryParams: { from: 'explore' } },
            paramMap: of(params),
          },
        },
      ],
    });
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    const fixture = TestBed.createComponent(MediaDetail);

    expect(fixture.componentInstance.backLabel).toBe('Volver al inicio');
    fixture.componentInstance.back();
    expect(navigate).toHaveBeenCalledWith(['/inicio'], { queryParams: {} });
  });
});

describe('MediaDetail album membership (L10B)', () => {
  function setup(params: Record<string, string>, queryParams: Record<string, string> = {}) {
    const paramMap = convertToParamMap(params);
    TestBed.configureTestingModule({
      imports: [MediaDetail],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { paramMap, queryParamMap: convertToParamMap(queryParams), queryParams }, paramMap: of(paramMap) },
        },
      ],
    });
    const http = TestBed.inject(HttpTestingController);
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    const fixture = TestBed.createComponent(MediaDetail);
    fixture.detectChanges();
    return { http, navigate, fixture };
  }

  function detail(overrides: Record<string, unknown>) {
    return {
      data: {
        media: { id: 9, album_id: null, file_type: 'video', title: 'Suelta', caption: null, taken_at: null, created_at: '2026-09-01T10:00:00', is_favorite: false },
        metadata: null, exif: null, tags: [], album_role: 'owner', album_capabilities: ALBUM_CAPABILITIES,
        ...overrides,
      },
      message: 'ok',
    };
  }

  const ownerAlbums = (request: { url: string; params: { get(name: string): string | null } }) =>
    request.url === '/api/albums' && request.params.get('role') === 'owner';

  it('opens a photo that is in no album from /recuerdos/:id and can add it to one', async () => {
    const { http, navigate, fixture } = setup({ mediaId: '9' });

    // Sin album de contexto no se pide ningun album ni su listado.
    http.expectOne('/api/media/9').flush(detail({ albums: [] }));
    expect(http.match(request => request.url.startsWith('/api/albums/')).length).toBe(0);
    http.expectOne(ownerAlbums).flush({
      data: [{ id: 3, titulo: 'Viaje', is_private: true, role: 'owner' }],
      message: 'ok',
      meta: { pagination: { page: 1, per_page: 60, total: 1, pages: 1 } },
    });
    fixture.detectChanges();
    await fixture.whenStable();

    const component = fixture.componentInstance;
    const section = () => fixture.nativeElement.querySelector('.album-membership') as HTMLElement;
    expect(component.backLabel).toBe('Volver a la biblioteca');
    expect(component.canSetCover()).toBe(false);
    expect(section().textContent).toContain('No está en ningún álbum');

    component.selectedAlbumToAdd = '3';
    component.addToAlbum();
    const put = http.expectOne('/api/albums/3/assets/9');
    expect(put.request.method).toBe('PUT');
    put.flush({ data: { album_id: 3, asset_id: 9, added: true }, message: 'ok' });
    fixture.detectChanges();
    await fixture.whenStable();
    expect(component.memberAlbums()).toEqual([{ id: 3, titulo: 'Viaje' }]);
    expect(section().querySelector('.album-chip')?.textContent).toContain('Viaje');

    component.back();
    expect(navigate).toHaveBeenCalledWith(['/biblioteca']);
  });

  it('removing the context album moves the view to the photo itself', async () => {
    const { http, navigate, fixture } = setup({ albumId: '3', mediaId: '9' }, { from: 'album' });
    vi.spyOn(TestBed.inject(Confirm), 'ask').mockResolvedValue(true);
    http.expectOne('/api/media/9?album_id=3').flush(detail({
      media: { id: 9, album_id: 3, file_type: 'video', title: 'Doble', caption: null, taken_at: null, created_at: '2026-09-01T10:00:00', is_favorite: false },
      albums: [{ id: 3, titulo: 'Viaje' }, { id: 4, titulo: 'Playa' }],
    }));
    fixture.detectChanges();
    await fixture.whenStable();

    await fixture.componentInstance.removeFromAlbum({ id: 3, titulo: 'Viaje' });
    const remove = http.expectOne('/api/albums/3/assets/9');
    expect(remove.request.method).toBe('DELETE');
    remove.flush({ data: { album_id: 3, asset_id: 9, remaining_albums: 1 }, message: 'ok' });

    expect(navigate).toHaveBeenCalledWith(['/recuerdos', 9], { queryParams: { from: 'album' }, replaceUrl: true });
  });

  it('never shows membership controls to a collaborator, whatever it can do', async () => {
    const { http, fixture } = setup({ albumId: '3', mediaId: '9' });
    http.expectOne('/api/media/9?album_id=3').flush(detail({
      media: { id: 9, album_id: 3, file_type: 'video', title: 'Ajena', caption: null, taken_at: null, created_at: '2026-09-01T10:00:00', is_favorite: false },
      // Ni con las cinco capacidades: gestionar pertenencias es solo del dueño.
      album_role: 'write', album_capabilities: ALBUM_CAPABILITIES,
    }));
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.componentInstance.canManageMemberships()).toBe(false);
    expect(fixture.nativeElement.querySelector('.album-membership')).toBeNull();
    expect(http.match(ownerAlbums).length).toBe(0);
  });

  // F05: los comentarios cuelgan debajo del detalle, para fotos y videos, y
  // van atados al recuerdo cargado (no al álbum ni a sus capacidades).
  for (const fileType of ['image', 'video'] as const) {
    it(`shows comments under a ${fileType} detail, keyed to the asset`, async () => {
      const params = convertToParamMap({ mediaId: '5' });
      TestBed.configureTestingModule({
        imports: [MediaDetail],
        providers: [
          provideHttpClient(), provideHttpClientTesting(), provideRouter([]),
          { provide: ActivatedRoute, useValue: { snapshot: { paramMap: params, queryParamMap: convertToParamMap({}) }, paramMap: of(params) } },
        ],
      });
      const http = TestBed.inject(HttpTestingController);
      const fixture = TestBed.createComponent(MediaDetail);
      fixture.detectChanges();
      http.expectOne('/api/media/5').flush({
        data: {
          media: { id: 5, album_id: null, file_type: fileType, title: 'Recuerdo', caption: null, taken_at: null, created_at: '2026-09-01T10:00:00', is_favorite: false },
          metadata: null, exif: null, tags: [], album_role: 'read', album_capabilities: [],
        },
        message: 'ok',
      });
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      const comments = http.match(request => request.url === '/api/media/5/comments');
      expect(comments.length).toBe(1);
      comments[0].flush({ data: [], message: 'ok', meta: { pagination: { page: 1, per_page: 20, total: 0, total_pages: 0 } } });
      fixture.detectChanges();
      const el = fixture.nativeElement as HTMLElement;
      expect(el.querySelector('.detail-comments app-media-comments')).not.toBeNull();
      expect(el.querySelector('#comment-draft'), 'read role still comments: the server decides').not.toBeNull();
    });
  }
});
