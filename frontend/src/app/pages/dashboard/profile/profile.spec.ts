import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { Profile } from './profile';

const PREFS = { notify_album_invites: true, notify_share_claimed: true, notify_shared_album_uploads: true };

async function setup() {
  TestBed.configureTestingModule({ imports: [Profile], providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])] });
  const http = TestBed.inject(HttpTestingController);
  const fixture = TestBed.createComponent(Profile);
  fixture.detectChanges();
  http.match(r => r.url !== '/api/notifications/preferences').forEach(r => r.flush({ data: [], message: 'ok' }));
  http.expectOne('/api/notifications/preferences').flush({ data: PREFS, message: 'ok' });
  await settle(fixture);
  return { http, fixture, el: fixture.nativeElement as HTMLElement };
}

async function settle(fixture: { detectChanges: () => void; whenStable: () => Promise<unknown> }) {
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
}

function toggle(el: HTMLElement, label: string): HTMLInputElement {
  const row = [...el.querySelectorAll('.notification-prefs label')].find(l => l.textContent?.includes(label));
  return row!.querySelector('input[type=checkbox]') as HTMLInputElement;
}

describe('Profile notification preferences (L12)', () => {
  it('shows exactly the three in-app categories and nothing about browser push', async () => {
    const { el } = await setup();
    const section = el.querySelector('.notification-prefs')!;
    expect(section.textContent).toContain('Notificaciones');
    const labels = [...section.querySelectorAll('label')].map(l => l.textContent || '');
    expect(labels.length).toBe(3);
    expect(labels[0]).toContain('Invitaciones a álbumes');
    expect(labels[1]).toContain('Invitaciones aceptadas');
    expect(labels[2]).toContain('Nuevas fotos en álbumes compartidos');
    for (const label of ['Invitaciones a álbumes', 'Invitaciones aceptadas', 'Nuevas fotos en álbumes compartidos']) {
      expect(toggle(el, label).checked).toBe(true);
    }
    expect(section.textContent).not.toMatch(/push|navegador|Permitir notificaciones/i);
  });

  it('saves one category with a partial PATCH and keeps the others', async () => {
    const { http, el, fixture } = await setup();
    const uploads = toggle(el, 'Nuevas fotos en álbumes compartidos');
    uploads.click();
    const patch = http.expectOne('/api/notifications/preferences');
    expect(patch.request.method).toBe('PATCH');
    expect(patch.request.body).toEqual({ notify_shared_album_uploads: false });
    patch.flush({ data: { ...PREFS, notify_shared_album_uploads: false }, message: 'ok' });
    await settle(fixture);
    expect(toggle(el, 'Nuevas fotos en álbumes compartidos').checked).toBe(false);
    expect(toggle(el, 'Invitaciones a álbumes').checked).toBe(true);
  });

  it('puts the switch back when the save fails', async () => {
    const { http, el, fixture } = await setup();
    toggle(el, 'Invitaciones aceptadas').click();
    http.expectOne('/api/notifications/preferences').flush({ message: 'boom' }, { status: 500, statusText: 'Server Error' });
    await settle(fixture);
    expect(toggle(el, 'Invitaciones aceptadas').checked).toBe(true);
  });
});
