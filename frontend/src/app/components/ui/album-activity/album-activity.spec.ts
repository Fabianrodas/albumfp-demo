import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { AlbumActivity } from './album-activity';
import { ALBUM_CAPABILITIES } from '../../../core/models/album-permissions';

const [UPLOAD] = ALBUM_CAPABILITIES;

const ana = { id: 1, username: 'ana', full_name: 'Ariana' };
const fab = { id: 2, username: 'fabian', full_name: 'Fabián' };
const page = (data: object[], total = data.length) =>
  ({ data, message: 'ok', meta: { pagination: { page: 1, per_page: 20, total, total_pages: Math.ceil(total / 20) } } });

async function setup() {
  TestBed.configureTestingModule({ imports: [AlbumActivity], providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])] });
  const http = TestBed.inject(HttpTestingController);
  const fixture = TestBed.createComponent(AlbumActivity);
  fixture.componentRef.setInput('albumId', 4);
  fixture.detectChanges();
  return { http, fixture, el: fixture.nativeElement as HTMLElement };
}

async function settle(fixture: { detectChanges: () => void; whenStable: () => Promise<unknown> }) {
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
}

describe('AlbumActivity', () => {
  it('lists human-readable entries newest first, with no raw ids, event names or JSON', async () => {
    const { http, fixture, el } = await setup();
    http.expectOne(r => r.url === '/api/albums/4/activity').flush(page([
      { id: 3, event_type: 'share_permission_changed', actor: ana, target: fab, subject: null,
        details: { permission: 'write', capabilities: [UPLOAD] }, created_at: '2026-09-23T12:00:00' },
      { id: 2, event_type: 'asset_uploaded', actor: fab, target: null,
        subject: { id: 81, file_type: 'image', available: true }, details: {}, created_at: '2026-09-23T11:00:00' },
      { id: 1, event_type: 'album_invite_created', actor: ana, target: fab, subject: null,
        details: { permission: 'read', capabilities: [] }, created_at: '2026-09-23T10:00:00' },
    ]));
    await settle(fixture);

    const items = [...el.querySelectorAll('.activity__item')].map(li => li.textContent || '');
    expect(items[0]).toContain('Ariana cambió los permisos de Fabián.');
    expect(items[0]).toContain('Puede: Subir fotos y videos');
    expect(items[1]).toContain('Fabián subió un recuerdo.');
    expect(items[2]).toContain('Ariana invitó a Fabián.');
    expect(el.textContent).not.toMatch(/share_permission_changed|asset_uploaded|\{"|target_user_id/);
    const ver = el.querySelector('a.activity__subject') as HTMLAnchorElement;
    expect(ver.getAttribute('href')).toBe('/albumes/4/media/81');
  });

  it('keeps working when the actor or the asset no longer exists', async () => {
    const { http, fixture, el } = await setup();
    http.expectOne(r => r.url === '/api/albums/4/activity').flush(page([
      { id: 2, event_type: 'asset_removed', actor: null, target: null, subject: null, details: {}, created_at: '2026-09-23T11:00:00' },
      { id: 1, event_type: 'asset_added', actor: ana, target: null,
        subject: { id: 9, file_type: 'video', available: false }, details: {}, created_at: '2026-09-23T10:00:00' },
    ]));
    await settle(fixture);
    expect(el.textContent).toContain('Alguien quitó un recuerdo del álbum.');
    expect(el.textContent).toContain('Recuerdo no disponible');
    expect(el.querySelector('a.activity__subject')).toBeNull();
  });

  it('pages with a manual load-more and shows an empty state and a forbidden state', async () => {
    const { http, fixture, el } = await setup();
    http.expectOne(r => r.url === '/api/albums/4/activity').flush(page([
      { id: 1, event_type: 'asset_added', actor: ana, target: null, subject: null, details: {}, created_at: '2026-09-23T10:00:00' },
    ], 21));
    await settle(fixture);
    const more = [...el.querySelectorAll('button')].find(b => b.textContent?.includes('Cargar más')) as HTMLButtonElement;
    more.click();
    http.expectOne(r => r.url === '/api/albums/4/activity' && r.params.get('page') === '2').flush(
      { message: 'No autorizado para ver la actividad de este álbum' }, { status: 403, statusText: 'Forbidden' });
    await settle(fixture);
    expect(el.textContent).toContain('No pudimos cargar la actividad');

    const otro = await (async () => {
      TestBed.resetTestingModule();
      return setup();
    })();
    otro.http.expectOne(r => r.url === '/api/albums/4/activity').flush(page([]));
    await settle(otro.fixture);
    expect(otro.el.textContent).toContain('Todavía no hay actividad');
  });
});
