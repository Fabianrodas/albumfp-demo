import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { Auth } from '../../../core/services/auth';
import { Home, localCalendarDate } from './home';

const video = (id: number, year: number) => ({
  id, user_id: 9, album_id: 4, file_type: 'video' as const, title: `Recuerdo ${id}`, caption: null,
  is_favorite: false, taken_at: `${year}-09-19T10:00:00`, created_at: `${year}-09-20T10:00:00`,
  album_titulo: 'Familia', memory_year: year, years_ago: 2026 - year, year_total: 1,
});

const home = (items: unknown[]) => ({
  data: { local_date: '2026-09-19', on_this_day: { items, total: items.length, per_year_limit: 12 } },
  message: 'ok',
});

/** F01/F02: Inicio = Explorar (principal) + «En este día» solo si hay algo +
 * Personas plegado. Sin Añadidos recientemente, Álbumes recientes ni
 * Compartido contigo: tienen su vista canónica. */
describe('Inicio (v1)', () => {
  let http: HttpTestingController;
  afterEach(() => http?.verify());

  function create(panel?: string) {
    TestBed.configureTestingModule({
      imports: [Home],
      providers: [
        provideHttpClient(), provideHttpClientTesting(), provideRouter([]),
        { provide: ActivatedRoute, useValue: { snapshot: { queryParamMap: convertToParamMap(panel ? { panel } : {}) } } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    TestBed.inject(Auth).user.set({ id: 9, username: 'ana', full_name: 'Ana', has_avatar: false, created_at: '2026-01-01' });
    const fixture = TestBed.createComponent(Home);
    fixture.detectChanges();
    return fixture;
  }

  const feed = () => http.expectOne(candidate => candidate.url === '/api/media/public')
    .flush({ data: [], message: 'ok', meta: { pagination: { page: 1, per_page: 12, total: 0, total_pages: 0 } } });
  const people = () => http.expectOne(candidate => candidate.url === '/api/users')
    .flush({ data: [], message: 'ok', meta: { pagination: { total_pages: 0 } } });

  async function settle(fixture: { detectChanges: () => void; whenStable: () => Promise<unknown> }) {
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  }

  it('formats a local calendar date without converting it through UTC', () => {
    expect(localCalendarDate(new Date(2026, 8, 9, 23, 30))).toBe('2026-09-09');
  });

  it('makes Explore the primary content and keeps People collapsed and unloaded', async () => {
    const fixture = create();
    const request = http.expectOne(candidate => candidate.url === '/api/home');
    expect(request.request.params.get('local_date')).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    request.flush(home([]));
    feed();
    http.expectNone(candidate => candidate.url === '/api/users');
    await settle(fixture);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('app-explore-feed')).not.toBeNull();
    expect(el.textContent).toContain('Fotos públicas de la comunidad');
    expect(el.querySelector('app-people-directory')).toBeNull();
    expect(el.querySelector('.people-panel button')?.getAttribute('aria-expanded')).toBe('false');
    for (const gone of ['Añadidos recientemente', 'Álbumes recientes', 'Compartido contigo', 'En este día']) {
      expect(el.textContent, gone).not.toContain(gone);
    }
  });

  it('shows On this day above the feed only when there are memories, grouped by year', async () => {
    const fixture = create();
    http.expectOne(candidate => candidate.url === '/api/home').flush(home([video(20, 2021), video(10, 2019)]));
    feed();
    await settle(fixture);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('En este día');
    expect(el.textContent).toContain('Hace 5 años');
    expect(el.textContent).toContain('Hace 7 años');
    const memories = el.querySelector('.memories-section')!;
    const explore = el.querySelector('app-explore-feed')!;
    expect(memories.compareDocumentPosition(explore) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('renders memories that belong to no album', async () => {
    const fixture = create();
    http.expectOne(candidate => candidate.url === '/api/home')
      .flush(home([{ ...video(20, 2021), album_id: null, album_titulo: null }]));
    feed();
    await settle(fixture);
    expect(fixture.nativeElement.querySelectorAll('.memories-section .media-card').length).toBe(1);
  });

  it('opens People on demand', async () => {
    const fixture = create();
    http.expectOne(candidate => candidate.url === '/api/home').flush(home([]));
    feed();
    await settle(fixture);
    (fixture.nativeElement.querySelector('.people-panel button') as HTMLButtonElement).click();
    await settle(fixture);
    people();
    expect(fixture.nativeElement.querySelector('app-people-directory')).not.toBeNull();
  });

  it('opens People when arriving from the old /usuarios URL', async () => {
    const fixture = create('personas');
    http.expectOne(candidate => candidate.url === '/api/home').flush(home([]));
    feed();
    people();
    await settle(fixture);
    expect(fixture.nativeElement.querySelector('.people-panel button').getAttribute('aria-expanded')).toBe('true');
  });

  it('offers a retry when the personal read fails, without hiding Explore', async () => {
    const fixture = create();
    http.expectOne(candidate => candidate.url === '/api/home').flush({ message: 'No disponible' }, { status: 503, statusText: 'Unavailable' });
    feed();
    await settle(fixture);
    expect(fixture.nativeElement.textContent).toContain('No disponible');
    expect(fixture.nativeElement.querySelector('app-explore-feed')).not.toBeNull();
    (fixture.nativeElement.querySelector('.home-error button') as HTMLButtonElement).click();
    http.expectOne(candidate => candidate.url === '/api/home').flush(home([]));
  });
});
