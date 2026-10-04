import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { Confirm } from '../../../core/services/confirm';
import { Profile } from './profile';

const PASSKEYS = [
  { id: 7, nickname: 'Portátil', created_at: '2026-09-20T10:00:00', last_used_at: null, transports: ['internal'] },
];

async function setup() {
  TestBed.configureTestingModule({ imports: [Profile], providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])] });
  const http = TestBed.inject(HttpTestingController);
  const fixture = TestBed.createComponent(Profile);
  fixture.detectChanges();
  http.match(() => true).forEach(r => r.flush({
    data: r.request.url === '/auth/passkeys' ? PASSKEYS
      : r.request.url === '/auth/recovery-codes' ? { total: 0, remaining: 0, created_at: null }
        : r.request.url.endsWith('preferences') ? { notify_album_invites: true, notify_share_claimed: true, notify_shared_album_uploads: true }
          : [],
    message: 'ok',
  }));
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
  return { http, fixture, profile: fixture.componentInstance, el: fixture.nativeElement as HTMLElement };
}

describe('Profile passkeys (L15)', () => {
  afterEach(() => vi.restoreAllMocks());

  it('lists each passkey by name and dates only', async () => {
    const { el } = await setup();
    const section = el.querySelector('.passkeys')!;
    expect(section.textContent).toContain('Portátil');
    expect(section.textContent).toContain('sin usar todavía');
  });

  it('sends the password and the nickname, never storing either', async () => {
    const { profile, http } = await setup();
    const storage = vi.spyOn(Storage.prototype, 'setItem');
    const created = {
      id: 'CQo', rawId: new Uint8Array([9, 10]).buffer, type: 'public-key', authenticatorAttachment: 'platform',
      getClientExtensionResults: () => ({}),
      response: { clientDataJSON: new Uint8Array([1]).buffer, attestationObject: new Uint8Array([2]).buffer, getTransports: () => ['internal'] },
    };
    Object.defineProperty(navigator, 'credentials', { configurable: true, value: { create: vi.fn().mockResolvedValue(created) } });

    profile.openAddPasskey();
    profile.passkeyNickname = ' Teléfono ';
    profile.passkeyPassword = 'mi contraseña actual';
    const done = profile.confirmAddPasskey();
    const options = http.expectOne(r => r.url === '/auth/passkeys/register/options');
    expect(options.request.body).toEqual({ current_password: 'mi contraseña actual' });
    expect(profile.passkeyPassword).toBe('');
    options.flush({ data: { challenge: 'AAEC', rp: { id: 'localhost', name: 'AlbumFP' }, user: { id: 'AAAAAAAN6Ls', name: 'ana', displayName: 'Ana' }, pubKeyCredParams: [] }, message: 'ok' });
    await Promise.resolve(); await Promise.resolve();
    const verify = http.expectOne(r => r.url === '/auth/passkeys/register/verify');
    expect(verify.request.body.nickname).toBe('Teléfono');
    expect(verify.request.body.credential.response.transports).toEqual(['internal']);
    verify.flush({ data: PASSKEYS[0], message: 'ok' }, { status: 201, statusText: 'Created' });
    await done;
    http.expectOne('/auth/passkeys').flush({ data: PASSKEYS, message: 'ok' });
    expect(profile.addingPasskey()).toBe(false);
    expect(storage).not.toHaveBeenCalled();
  });

  it('revokes only after confirming', async () => {
    const { profile, http } = await setup();
    const ask = vi.spyOn(TestBed.inject(Confirm), 'ask').mockResolvedValue(true);
    await profile.deletePasskey(PASSKEYS[0]);
    expect(ask).toHaveBeenCalled();
    http.expectOne(r => r.url === '/auth/passkeys/7' && r.method === 'DELETE').flush({ message: 'ok' });
  });
});
