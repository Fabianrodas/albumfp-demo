import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter, Router } from '@angular/router';
import { NotificationBell } from './notification-bell';

const ana = { id: 1, username: 'ana', full_name: 'Ariana' };
const page = (data: object[], total = data.length) =>
  ({ data, message: 'ok', meta: { pagination: { page: 1, per_page: 20, total, total_pages: Math.ceil(total / 20) } } });
const invite = { id: 11, event_type: 'album_invite', album_id: 7, album_title: 'Viaje', actor: ana, read_at: null, created_at: '2026-09-23T10:00:00' };
const upload = { id: 10, event_type: 'shared_album_upload', album_id: 7, album_title: 'Viaje', actor: ana, read_at: '2026-09-22T10:00:00', created_at: '2026-09-22T09:00:00' };

async function setup(unread = 1) {
  TestBed.configureTestingModule({ imports: [NotificationBell], providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])] });
  const http = TestBed.inject(HttpTestingController);
  const fixture = TestBed.createComponent(NotificationBell);
  fixture.detectChanges();
  http.expectOne('/api/notifications/unread-count').flush({ data: { unread }, message: 'ok' });
  await settle(fixture);
  const el = fixture.nativeElement as HTMLElement;
  return { http, fixture, el, bell: fixture.componentInstance, trigger: el.querySelector('button.bell__trigger') as HTMLButtonElement };
}

async function settle(fixture: { detectChanges: () => void; whenStable: () => Promise<unknown> }) {
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
}

describe('NotificationBell', () => {
  it('is an accessible button whose badge shows the unread count and hides at zero', async () => {
    const { el, trigger, fixture, bell } = await setup(3);
    expect(trigger.getAttribute('aria-label')).toBe('Notificaciones, 3 sin leer');
    expect(el.querySelector('.bell__badge')?.textContent?.trim()).toBe('3');

    bell.unread.set(0);
    await settle(fixture);
    expect(el.querySelector('.bell__badge')).toBeNull();
    expect(trigger.getAttribute('aria-label')).toBe('Notificaciones');

    bell.unread.set(250);
    await settle(fixture);
    expect(el.querySelector('.bell__badge')?.textContent?.trim()).toBe('99+');
  });

  it('opening refreshes the list and the count without marking anything read', async () => {
    const { http, el, trigger, fixture } = await setup(1);
    trigger.click();
    await settle(fixture);
    expect(el.textContent).toContain('Cargando');
    http.expectOne(r => r.url === '/api/notifications').flush(page([invite, upload]));
    http.expectOne('/api/notifications/unread-count').flush({ data: { unread: 1 }, message: 'ok' });
    await settle(fixture);

    const items = el.querySelectorAll('.bell__item');
    expect(items.length).toBe(2);
    expect(items[0].classList).toContain('is-unread');
    expect(items[1].classList).not.toContain('is-unread');
    expect(items[0].textContent).toContain('Ariana te invitó a «Viaje».');
    http.expectNone(r => r.method !== 'GET');
  });

  it('clicking a notification marks it read, lowers the badge and navigates to its safe destination', async () => {
    const { http, el, trigger, fixture, bell } = await setup(1);
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigateByUrl').mockResolvedValue(true);
    trigger.click();
    await settle(fixture);
    http.expectOne(r => r.url === '/api/notifications').flush(page([invite]));
    http.expectOne('/api/notifications/unread-count').flush({ data: { unread: 1 }, message: 'ok' });
    await settle(fixture);

    (el.querySelector('.bell__item') as HTMLButtonElement).click();
    const read = http.expectOne('/api/notifications/11/read');
    expect(read.request.method).toBe('PATCH');
    read.flush({ data: { id: 11, read_at: '2026-09-23T11:00:00' }, message: 'ok' });
    await settle(fixture);

    expect(bell.unread()).toBe(0);
    expect(navigate).toHaveBeenCalledWith('/compartido');
    expect(bell.open()).toBe(false);
  });

  it('marks everything read only when asked to', async () => {
    const { http, el, trigger, fixture, bell } = await setup(2);
    trigger.click();
    await settle(fixture);
    http.expectOne(r => r.url === '/api/notifications').flush(page([invite, { ...upload, read_at: null }]));
    http.expectOne('/api/notifications/unread-count').flush({ data: { unread: 2 }, message: 'ok' });
    await settle(fixture);

    const all = [...el.querySelectorAll('button')].find(b => b.textContent?.includes('Marcar todas como leídas')) as HTMLButtonElement;
    all.click();
    http.expectOne('/api/notifications/read-all').flush({ data: { updated: 2 }, message: 'ok' });
    await settle(fixture);
    expect(bell.unread()).toBe(0);
    expect(el.querySelectorAll('.bell__item.is-unread').length).toBe(0);
  });

  it('shows an empty state, a load-more control, and an error that keeps the bell usable', async () => {
    const { http, el, trigger, fixture } = await setup(0);
    trigger.click();
    await settle(fixture);
    http.expectOne(r => r.url === '/api/notifications').flush(page([]));
    http.expectOne('/api/notifications/unread-count').flush({ data: { unread: 0 }, message: 'ok' });
    await settle(fixture);
    expect(el.textContent).toContain('No tienes notificaciones');

    trigger.click();                    // cerrar
    trigger.click();                    // y volver a abrir: vuelve a pedir
    await settle(fixture);
    http.expectOne(r => r.url === '/api/notifications').flush(page([invite], 25));
    http.expectOne('/api/notifications/unread-count').flush({ data: { unread: 1 }, message: 'ok' });
    await settle(fixture);
    const more = [...el.querySelectorAll('button')].find(b => b.textContent?.includes('Cargar más')) as HTMLButtonElement;
    more.click();
    http.expectOne(r => r.url === '/api/notifications' && r.params.get('page') === '2')
      .flush({ message: 'boom' }, { status: 500, statusText: 'Server Error' });
    await settle(fixture);
    expect(el.textContent).toContain('No pudimos cargar tus notificaciones');
    expect(el.querySelector('button.bell__trigger')).not.toBeNull();
  });

  it('never asks the browser for notification permission', async () => {
    const request = vi.fn();
    (globalThis as { Notification?: unknown }).Notification = { requestPermission: request, permission: 'default' };
    const { http, trigger, fixture } = await setup(1);
    trigger.click();
    await settle(fixture);
    http.expectOne(r => r.url === '/api/notifications').flush(page([invite]));
    http.expectOne('/api/notifications/unread-count').flush({ data: { unread: 1 }, message: 'ok' });
    expect(request).not.toHaveBeenCalled();
    delete (globalThis as { Notification?: unknown }).Notification;
  });
});
