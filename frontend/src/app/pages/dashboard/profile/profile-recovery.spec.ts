import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { Confirm } from '../../../core/services/confirm';
import { Profile } from './profile';

const CODES = Array.from({ length: 10 }, (_, i) => `AAAA${'ABCDEFGHIJ'[i]}-BBBBB-CCCCC-DDDDD-EEEEE-FFFFF`);

async function setup(status = { total: 0, remaining: 0, created_at: null as string | null }) {
  TestBed.configureTestingModule({ imports: [Profile], providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])] });
  const http = TestBed.inject(HttpTestingController);
  const fixture = TestBed.createComponent(Profile);
  fixture.detectChanges();
  http.match(() => true).forEach(r => r.flush({
    data: r.request.url === '/auth/recovery-codes' ? status
      : r.request.url.endsWith('preferences') ? { notify_album_invites: true, notify_share_claimed: true, notify_shared_album_uploads: true }
        : [],
    message: 'ok',
  }));
  await settle(fixture);
  return { http, fixture, profile: fixture.componentInstance, el: fixture.nativeElement as HTMLElement };
}

async function settle(fixture: { detectChanges: () => void; whenStable: () => Promise<unknown> }) {
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
}

async function generate(ctx: Awaited<ReturnType<typeof setup>>) {
  const { el, fixture, http, profile } = ctx;
  (el.querySelector('.recovery-codes button.btn-brand') as HTMLButtonElement).click();
  await settle(fixture);
  profile.recoveryPassword = 'mi contraseña actual';
  profile.confirmGenerate();
  const req = http.expectOne(r => r.url === '/auth/recovery-codes' && r.method === 'POST');
  expect(req.request.body).toEqual({ current_password: 'mi contraseña actual' });
  req.flush({ data: { codes: CODES, created_at: '2026-09-24T10:00:00' }, message: 'ok' }, { status: 201, statusText: 'Created' });
  http.expectOne(r => r.url === '/auth/recovery-codes' && r.method === 'GET')
    .flush({ data: { total: 10, remaining: 10, created_at: '2026-09-24T10:00:00' }, message: 'ok' });
  await settle(fixture);
}

describe('Profile recovery codes (L14)', () => {
  it('shows the status without ever showing a code', async () => {
    const { el } = await setup({ total: 10, remaining: 7, created_at: '2026-09-01T10:00:00' });
    const section = el.querySelector('.recovery-codes')!;
    expect(section.textContent).toContain('Códigos de recuperación');
    expect(section.textContent).toContain('7 de 10');
    expect(section.textContent).not.toMatch(/[A-Z2-7]{5}-[A-Z2-7]{5}/);
  });

  it('asks for the current password, reveals the ten codes once, and forgets them', async () => {
    const ctx = await setup();
    const storage = vi.spyOn(Storage.prototype, 'setItem');
    await generate(ctx);
    const { el, fixture, profile } = ctx;
    const shown = [...el.querySelectorAll('.recovery-reveal li')].map(li => li.textContent?.trim());
    expect(shown).toEqual(CODES);
    expect(profile.recoveryPassword, 'la contraseña no se queda en memoria').toBe('');

    (([...el.querySelectorAll('button')].find(b => b.textContent?.includes('Ya los guardé'))) as HTMLButtonElement).click();
    await settle(fixture);
    expect(profile.revealedCodes()).toEqual([]);
    expect(el.textContent).not.toContain(CODES[0]);
    expect(storage).not.toHaveBeenCalled();
  });

  it('downloads the codes as a text file from memory and leaves nothing behind', async () => {
    const ctx = await setup();
    await generate(ctx);
    const created = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:codes');
    const revoked = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    ctx.profile.downloadCodes();
    const blob = created.mock.calls[0][0] as Blob;
    expect(await blob.text()).toContain(CODES[9]);
    expect(click).toHaveBeenCalled();
    expect(revoked).toHaveBeenCalledWith('blob:codes');
  });

  it('forgets the codes when the page goes away', async () => {
    const ctx = await setup();
    await generate(ctx);
    ctx.fixture.destroy();
    expect(ctx.profile.revealedCodes()).toEqual([]);
  });

  it('revokes every code after confirming', async () => {
    const { el, http, fixture } = await setup({ total: 10, remaining: 10, created_at: '2026-09-01T10:00:00' });
    vi.spyOn(TestBed.inject(Confirm), 'ask').mockResolvedValue(true);
    (([...el.querySelectorAll('.recovery-codes button')].find(b => b.textContent?.includes('Revocar'))) as HTMLButtonElement).click();
    await settle(fixture);
    http.expectOne(r => r.url === '/auth/recovery-codes' && r.method === 'DELETE').flush({ data: { revoked: 10 }, message: 'ok' });
    http.expectOne(r => r.url === '/auth/recovery-codes' && r.method === 'GET')
      .flush({ data: { total: 0, remaining: 0, created_at: null }, message: 'ok' });
    await settle(fixture);
    expect(el.querySelector('.recovery-codes')!.textContent).toContain('No tienes códigos');
  });
});
