import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { Library } from './library';

class NeverVisibleObserver implements IntersectionObserver {
  readonly root = null;
  readonly rootMargin = '0px';
  readonly scrollMargin = '0px';
  readonly thresholds = [0];
  disconnect() {}
  observe() {}
  takeRecords(): IntersectionObserverEntry[] { return []; }
  unobserve() {}
}

const media = (id: number, date: string) => ({
  id,
  user_id: 9,
  album_id: 4,
  album_titulo: 'Viajes',
  file_type: 'image',
  title: `Foto ${id}`,
  caption: null,
  is_favorite: false,
  taken_at: date,
  created_at: date,
  effective_date: date,
});

describe('Library timeline page', () => {
  let http: HttpTestingController;
  let originalObserver: typeof IntersectionObserver | undefined;

  beforeEach(() => {
    originalObserver = globalThis.IntersectionObserver;
    globalThis.IntersectionObserver = NeverVisibleObserver as unknown as typeof IntersectionObserver;
  });

  afterEach(() => {
    http?.verify();
    globalThis.IntersectionObserver = originalObserver as typeof IntersectionObserver;
  });

  /** F02: Biblioteca pide también «Añadidos recientemente». Por defecto se
   * responde vacío; `recent: 'open'` lo deja pendiente para la propia prueba. */
  function create(focus?: string, recent: unknown[] | 'open' = [], data: Record<string, unknown> = {}) {
    const queryParamMap = convertToParamMap(focus ? { focus } : {});
    TestBed.configureTestingModule({
      imports: [Library],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { snapshot: { queryParamMap, data } } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(Library);
    fixture.detectChanges();
    if (recent !== 'open' && !data['archived']) {
      http.expectOne(candidate => candidate.url === '/api/media/library/recent').flush({ data: recent, message: 'ok' });
    }
    return fixture;
  }

  const emptyTimeline = () => http.expectOne(candidate => candidate.url === '/api/media/library')
    .flush({ data: [], message: 'ok', meta: { next_cursor: null, total: 0, limit: 60 } });

  it('shows recently added media first, from the same library source', async () => {
    const fixture = create(undefined, [{ ...media(9, '2021-01-01T10:00:00'), album_id: null, album_titulo: null },
      media(8, '2026-09-20T10:00:00')]);
    emptyTimeline();
    fixture.detectChanges();
    await fixture.whenStable();
    const section = fixture.nativeElement.querySelector('.recent-section');
    expect(section.textContent).toContain('Añadidos recientemente');
    expect(section.querySelectorAll('.media-card').length).toBe(2);
  });

  it('hides the recent block when there is nothing', () => {
    const fixture = create();
    emptyTimeline();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.recent-section')).toBeNull();
    expect(fixture.nativeElement.querySelector('.recent-error')).toBeNull();
  });

  it('offers a retry when recent media fails to load', () => {
    const fixture = create(undefined, 'open');
    emptyTimeline();
    http.expectOne(candidate => candidate.url === '/api/media/library/recent')
      .flush({ message: 'x' }, { status: 503, statusText: 'Unavailable' });
    fixture.detectChanges();
    const retry = fixture.nativeElement.querySelector('.recent-error button') as HTMLButtonElement;
    expect(retry.textContent).toContain('Reintentar');
    retry.click();
    http.expectOne(candidate => candidate.url === '/api/media/library/recent').flush({ data: [], message: 'ok' });
  });

  it('never asks for recent media on the Archive page', () => {
    create(undefined, [], { archived: true });
    http.expectNone(candidate => candidate.url === '/api/media/library/recent');
    emptyTimeline();
  });

  it('renders server order grouped by year and month', async () => {
    const fixture = create();
    const request = http.expectOne(candidate => candidate.url === '/api/media/library');
    expect(request.request.params.get('limit')).toBe('60');
    expect(request.request.params.has('cursor')).toBe(false);
    request.flush({
      data: [media(3, '2026-09-18T10:00:00'), media(2, '2026-08-01T10:00:00')],
      message: 'ok',
      meta: { next_cursor: null, total: 2, limit: 60 },
    });

    fixture.detectChanges();
    await fixture.whenStable();
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('2026');
    expect(text).toContain('septiembre');
    expect(text).toContain('agosto');
    expect(fixture.nativeElement.querySelectorAll('.media-card').length).toBe(2);
  });

  it('lists an asset that is in no album, with no album context label', async () => {
    const fixture = create();
    http.expectOne(candidate => candidate.url === '/api/media/library').flush({
      data: [{ ...media(4, '2026-09-18T10:00:00'), album_id: null, album_titulo: null }],
      message: 'ok',
      meta: { next_cursor: null, total: 1, limit: 60 },
    });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.querySelectorAll('.media-card').length).toBe(1);
    expect(fixture.nativeElement.querySelector('.album-context')).toBeNull();
  });

  it('loads cursor pages until a requested focus item is present', async () => {
    const fixture = create('1');
    http.expectOne(candidate => candidate.url === '/api/media/library').flush({
      data: [media(3, '2026-09-18T10:00:00')],
      message: 'ok',
      meta: { next_cursor: 'next-page', total: 2, limit: 60 },
    });
    fixture.detectChanges();
    await fixture.whenStable();

    const next = http.expectOne(candidate =>
      candidate.url === '/api/media/library' && candidate.params.get('cursor') === 'next-page'
    );
    next.flush({
      data: [media(1, '2025-12-01T10:00:00')],
      message: 'ok',
      meta: { next_cursor: null, total: 2, limit: 60 },
    });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.querySelector('#media-1')).not.toBeNull();
  });
});

describe('Archive page (Library in archived mode)', () => {
  let http: HttpTestingController;
  let originalObserver: typeof IntersectionObserver | undefined;

  beforeEach(() => {
    originalObserver = globalThis.IntersectionObserver;
    globalThis.IntersectionObserver = NeverVisibleObserver as unknown as typeof IntersectionObserver;
  });

  afterEach(() => {
    http?.verify();
    globalThis.IntersectionObserver = originalObserver as typeof IntersectionObserver;
  });

  function create() {
    TestBed.configureTestingModule({
      imports: [Library],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { snapshot: { queryParamMap: convertToParamMap({}), data: { archived: true } } } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(Library);
    fixture.detectChanges();
    return fixture;
  }

  it('reads only archived media and presents itself as the archive', async () => {
    const fixture = create();
    const request = http.expectOne(candidate => candidate.url === '/api/media/library');
    expect(request.request.params.get('archived')).toBe('only');
    request.flush({ data: [media(3, '2020-05-01T10:00:00')], message: 'ok', meta: { next_cursor: null, total: 1, limit: 60 } });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.querySelector('h1').textContent).toContain('Archivo');
    expect(fixture.nativeElement.querySelector('app-media-grid')).not.toBeNull();
  });

  it('explains an empty archive instead of the empty library copy', async () => {
    const fixture = create();
    http.expectOne(candidate => candidate.url === '/api/media/library')
      .flush({ data: [], message: 'ok', meta: { next_cursor: null, total: 0, limit: 60 } });
    fixture.detectChanges();
    await fixture.whenStable();

    const text = fixture.nativeElement.textContent;
    expect(text).toContain('No has archivado nada');
    expect(text).not.toContain('Tu biblioteca está lista para empezar');
  });
});
