import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { ExploreFeed } from './explore-feed';

// Videos: la tarjeta no pide nada para ellos, así la prueba solo ve el feed.
const card = (id: number) => ({
  id, album_id: 7, file_type: 'video' as const, title: `Pública ${id}`,
  created_at: '2026-09-18T00:00:00',
  owner: { id: 2, username: 'bob', full_name: 'Bob', has_avatar: false },
});

describe('ExploreFeed (embedded in Inicio)', () => {
  let http: HttpTestingController;

  afterEach(() => http?.verify());

  function create() {
    TestBed.configureTestingModule({
      imports: [ExploreFeed],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    });
    http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(ExploreFeed);
    fixture.detectChanges();
    return fixture;
  }

  const feed = () => http.expectOne(candidate => candidate.url === '/api/media/public');

  it('renders the public community feed, paginated by the server', async () => {
    const fixture = create();
    const request = feed();
    expect(request.request.params.get('page')).toBe('1');
    expect(request.request.params.get('per_page')).toBe('12');
    expect(request.request.params.get('sort_by')).toBe('created_at');
    expect(request.request.params.get('sort_dir')).toBe('desc');
    request.flush({
      data: [card(1), card(2)], message: 'ok',
      meta: { pagination: { page: 1, per_page: 12, total: 30, total_pages: 3 } },
    });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.querySelector('h1'), 'an embedded section has no page title').toBeNull();
    expect(fixture.nativeElement.querySelector('h2').textContent).toContain('Fotos públicas');
    expect(fixture.nativeElement.querySelectorAll('app-public-media-card').length).toBe(2);
    expect(fixture.nativeElement.textContent).toContain('Página 1 de 3');
  });

  it('pages forward and restarts from page one when the order changes', () => {
    const fixture = create();
    vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined);
    feed().flush({ data: [card(1)], message: 'ok', meta: { pagination: { page: 1, per_page: 12, total: 30, total_pages: 3 } } });

    fixture.componentInstance.goTo(2);
    const second = feed();
    expect(second.request.params.get('page')).toBe('2');
    second.flush({ data: [card(3)], message: 'ok', meta: { pagination: { page: 2, per_page: 12, total: 30, total_pages: 3 } } });

    fixture.componentInstance.sortBy = 'taken_at';
    fixture.componentInstance.changeSort();
    const resorted = feed();
    expect(resorted.request.params.get('page')).toBe('1');
    expect(resorted.request.params.get('sort_by')).toBe('taken_at');
    resorted.flush({ data: [], message: 'ok', meta: { pagination: { page: 1, per_page: 12, total: 0, total_pages: 0 } } });
  });

  it('explains an empty feed', async () => {
    const fixture = create();
    feed().flush({ data: [], message: 'ok', meta: { pagination: { page: 1, per_page: 12, total: 0, total_pages: 0 } } });
    fixture.detectChanges();
    await fixture.whenStable();
    expect(fixture.nativeElement.textContent).toContain('Aún nadie ha compartido una foto.');
  });

  it('surfaces a failed load instead of claiming the feed is empty', async () => {
    const fixture = create();
    feed().flush({ message: 'Feed no disponible' }, { status: 503, statusText: 'Unavailable' });
    fixture.detectChanges();
    await fixture.whenStable();
    expect(fixture.nativeElement.textContent).toContain('Feed no disponible');
    expect(fixture.nativeElement.textContent).not.toContain('Aún nadie ha compartido una foto.');
  });
});
