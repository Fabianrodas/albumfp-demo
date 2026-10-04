import { TestBed } from '@angular/core/testing';
import { HttpEventType, provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { AlbumApi, PREVIEW_CACHE_BYTES } from './album-api';

describe('AlbumApi read cache', () => {
  let api: AlbumApi;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    api = TestBed.inject(AlbumApi);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('issues one request for a repeated read', () => {
    api.albums().subscribe();
    api.albums().subscribe();

    http.expectOne(request => request.url === '/api/albums').flush({ data: [], message: 'ok' });
  });

  it('treats different query params as different entries', () => {
    api.albums({ role: 'owner' }).subscribe();
    api.albums({ role: 'write' }).subscribe();

    const requests = http.match(request => request.url === '/api/albums');
    expect(requests.length).toBe(2);
    requests.forEach(request => request.flush({ data: [], message: 'ok' }));
  });

  it('sends pagination when loading the complete tag vocabulary', () => {
    api.tags('', undefined, { page: 2, perPage: 100 }).subscribe();

    const request = http.expectOne(candidate => candidate.url === '/api/tags');
    expect(request.request.params.get('page')).toBe('2');
    expect(request.request.params.get('per_page')).toBe('100');
    request.flush({ data: [], message: 'ok' });
  });

  it('refetches after a write invalidates the cache', () => {
    api.albums().subscribe();
    http.expectOne(request => request.url === '/api/albums').flush({ data: [], message: 'ok' });

    api.createAlbum({ titulo: 'Nuevo', descripcion: null, is_private: true }).subscribe();
    http.expectOne(request => request.method === 'POST' && request.url === '/api/albums')
      .flush({ data: {}, message: 'ok' });

    api.albums().subscribe();
    http.expectOne(request => request.method === 'GET' && request.url === '/api/albums')
      .flush({ data: [], message: 'ok' });
  });

  it('does not memoize a failed read', () => {
    api.favorites().subscribe({ error: () => {} });
    http.expectOne('/api/media/favorites').flush('boom', { status: 500, statusText: 'Server Error' });

    api.favorites().subscribe({ error: () => {} });
    http.expectOne('/api/media/favorites').flush('boom', { status: 500, statusText: 'Server Error' });
  });

  it('sends the library cursor and limit without offset pagination', () => {
    api.library('opaque-cursor', 40).subscribe();

    const request = http.expectOne(candidate => candidate.url === '/api/media/library');
    expect(request.request.params.get('cursor')).toBe('opaque-cursor');
    expect(request.request.params.get('limit')).toBe('40');
    expect(request.request.params.has('page')).toBe(false);
    request.flush({
      data: [],
      message: 'ok',
      meta: { next_cursor: null, total: 0, limit: 40 },
    });
  });

  it('loads the personal home with the browser calendar date in one request', () => {
    api.home('2026-09-19').subscribe();

    const request = http.expectOne(candidate => candidate.url === '/api/home');
    expect(request.request.params.get('local_date')).toBe('2026-09-19');
    request.flush({
      data: {
        local_date: '2026-09-19',
        on_this_day: { items: [], total: 0, per_year_limit: 12 },
      },
      message: 'ok',
    });
  });

  it('archives through the organize endpoint and reads the archive through the library', () => {
    api.archive(5, true).subscribe();
    const patch = http.expectOne('/api/media/5/archive');
    expect(patch.request.method).toBe('PATCH');
    expect(patch.request.body).toEqual({ archived: true });
    patch.flush({ data: { id: 5, album_id: 3, archived_at: '2026-09-19T10:00:00' }, message: 'ok' });

    // Un recuerdo suelto se archiva igual y no trae album de contexto.
    api.archive(6, true).subscribe();
    http.expectOne('/api/media/6/archive')
      .flush({ data: { id: 6, album_id: null, archived_at: '2026-09-19T10:00:00' }, message: 'ok' });

    api.library(null, 60, true).subscribe();
    const archive = http.expectOne(candidate => candidate.url === '/api/media/library');
    expect(archive.request.params.get('archived')).toBe('only');
    archive.flush({ data: [], message: 'ok', meta: { next_cursor: null, total: 0, limit: 60 } });

    api.library(null, 60).subscribe();
    const library = http.expectOne(candidate => candidate.url === '/api/media/library');
    expect(library.request.params.has('archived')).toBe(false);
    library.flush({ data: [], message: 'ok', meta: { next_cursor: null, total: 0, limit: 60 } });
  });

  it('exposes per-file upload progress without changing the secure endpoint', () => {
    const events: HttpEventType[] = [];
    api.createMediaWithProgress(9, { file: new File(['photo'], 'uno.jpg', { type: 'image/jpeg' }) })
      .subscribe(event => events.push(event.type));

    const request = http.expectOne(candidate =>
      candidate.method === 'POST' && candidate.url === '/api/albums/9/media');
    expect(request.request.reportProgress).toBe(true);
    expect(request.request.body instanceof FormData).toBe(true);
    request.event({ type: HttpEventType.UploadProgress, loaded: 5, total: 10 });
    request.flush({ data: { id: 4 }, message: 'ok' });

    expect(events).toContain(HttpEventType.UploadProgress);
    expect(events).toContain(HttpEventType.Response);
  });

  it('sends force_duplicate only after explicit confirmation', () => {
    const file = new File(['photo'], 'duplicada.jpg', { type: 'image/jpeg' });
    api.createMedia(9, { file }).subscribe();
    const normal = http.expectOne('/api/albums/9/media');
    expect((normal.request.body as FormData).get('force_duplicate')).toBeNull();
    normal.flush({ data: { id: 1 }, message: 'ok' });

    api.createMedia(9, { file, force_duplicate: true }).subscribe();
    const forced = http.expectOne('/api/albums/9/media');
    expect((forced.request.body as FormData).get('force_duplicate')).toBe('true');
    forced.flush({ data: { id: 2 }, message: 'ok' });
  });
});

describe('AlbumApi preview reuse', () => {
  let api: AlbumApi;
  let http: HttpTestingController;
  const preview = (id: number) => `/api/media/${id}/preview`;
  const bytes = (n: number) => new Blob([new Uint8Array(n)]);

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    api = TestBed.inject(AlbumApi);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  // Medido en producción: volver a una vista re-descargaba cada preview
  // (~141 KB y ~750 ms por el tramo Rack→Dell) aunque ya estuviera vista.
  it('reuses a preview already downloaded instead of fetching it again', () => {
    const blob = bytes(10);
    api.mediaPreview(7).subscribe();
    http.expectOne(preview(7)).flush(blob);

    let again: Blob | undefined;
    api.mediaPreview(7).subscribe(b => again = b);
    http.expectNone(preview(7));
    expect(again).toBe(blob);
  });

  it('keeps previews across writes, because their bytes never change', () => {
    api.mediaPreview(7).subscribe();
    http.expectOne(preview(7)).flush(bytes(10));

    api.favorite(7, true).subscribe();
    http.expectOne(request => request.method !== 'GET').flush({ data: {}, message: 'ok' });

    api.mediaPreview(7).subscribe();
    http.expectNone(preview(7));
  });

  it('does not keep a failed preview', () => {
    api.mediaPreview(7).subscribe({ error: () => {} });
    http.expectOne(preview(7)).flush(null, { status: 503, statusText: 'Unavailable' });

    api.mediaPreview(7).subscribe();
    http.expectOne(preview(7)).flush(bytes(10));
  });

  it('never keeps originals', () => {
    api.mediaFile(7).subscribe();
    http.expectOne('/api/media/7/file').flush(bytes(10));
    api.mediaFile(7).subscribe();
    http.expectOne('/api/media/7/file').flush(bytes(10));
  });

  it('evicts the least recently used preview beyond the byte budget', () => {
    const quarter = Math.floor(PREVIEW_CACHE_BYTES / 4);
    for (const id of [1, 2, 3, 4]) {            // caben justas
      api.mediaPreview(id).subscribe();
      http.expectOne(preview(id)).flush(bytes(quarter));
    }

    api.mediaPreview(1).subscribe();            // 1 pasa a ser la más reciente
    http.expectNone(preview(1));

    api.mediaPreview(5).subscribe();            // no cabe: sale 2, no 1
    http.expectOne(preview(5)).flush(bytes(quarter));

    api.mediaPreview(1).subscribe();
    http.expectNone(preview(1));
    api.mediaPreview(2).subscribe();
    http.expectOne(preview(2)).flush(bytes(10));
  });

  // /preview cae al original cuando no hay derivado (un video de portada puede
  // pesar cientos de MB): uno así no puede vaciar el resto del presupuesto.
  it('does not let an oversized fallback flush the kept previews', () => {
    api.mediaPreview(1).subscribe();
    http.expectOne(preview(1)).flush(bytes(10));
    api.mediaPreview(9).subscribe();
    http.expectOne(preview(9)).flush(bytes(PREVIEW_CACHE_BYTES + 1));

    api.mediaPreview(1).subscribe();
    http.expectNone(preview(1));
    api.mediaPreview(9).subscribe();
    http.expectOne(preview(9)).flush(bytes(10));
  });

  it('forgets every preview when the session ends', () => {
    api.mediaPreview(7).subscribe();
    http.expectOne(preview(7)).flush(bytes(10));

    api.forgetSession();

    api.mediaPreview(7).subscribe();
    http.expectOne(preview(7)).flush(bytes(10));
  });
});
