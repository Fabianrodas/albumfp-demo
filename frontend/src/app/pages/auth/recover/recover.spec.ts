import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { Recover } from './recover';

const CODE = 'ABCDE-FGHIJ-KLMNO-PQRST-UVWXY-Z2345';
const PASSWORD = 'Una frase nueva y bastante larga 2026';

async function setup() {
  TestBed.configureTestingModule({ imports: [Recover], providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])] });
  const http = TestBed.inject(HttpTestingController);
  const fixture = TestBed.createComponent(Recover);
  fixture.detectChanges();
  return { http, fixture, page: fixture.componentInstance, el: fixture.nativeElement as HTMLElement };
}

async function settle(fixture: { detectChanges: () => void; whenStable: () => Promise<unknown> }) {
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
}

function fill(page: Recover, values: Partial<Record<'username' | 'code' | 'password' | 'confirm', string>>) {
  if (values.username !== undefined) page.username.set(values.username);
  if (values.code !== undefined) page.code.set(values.code);
  if (values.password !== undefined) page.password.set(values.password);
  if (values.confirm !== undefined) page.confirm.set(values.confirm);
}

describe('Recover (L14)', () => {
  it('validates locally before asking the server', async () => {
    const { page, fixture, el, http } = await setup();
    fill(page, { username: 'ana', code: 'corto', password: 'corta', confirm: 'otra' });
    page.submit();
    await settle(fixture);
    expect(el.textContent).toContain('código tiene 30 caracteres');
    expect(el.textContent).toContain('15');
    expect(el.textContent).toContain('no coinciden');
    http.expectNone('/auth/recover');
  });

  it('sends username, the normalised code and the new password, then points to login', async () => {
    const { page, fixture, el, http } = await setup();
    const storage = vi.spyOn(Storage.prototype, 'setItem');
    fill(page, { username: ' ana ', code: CODE.toLowerCase().replace(/-/g, ' '), password: PASSWORD, confirm: PASSWORD });
    page.submit();
    const req = http.expectOne('/auth/recover');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ username: 'ana', code: CODE, new_password: PASSWORD });
    req.flush({ message: 'ok' });
    await settle(fixture);
    expect(el.textContent).toContain('Contraseña restablecida');
    expect(el.querySelector('a[href="/login"]')).not.toBeNull();
    expect(page.code()).toBe('');
    expect(page.password()).toBe('');
    expect(storage).not.toHaveBeenCalled();
  });

  it('shows the server\'s generic refusal without guessing why', async () => {
    const { page, fixture, el, http } = await setup();
    fill(page, { username: 'ana', code: CODE, password: PASSWORD, confirm: PASSWORD });
    page.submit();
    http.expectOne('/auth/recover').flush(
      { message: 'No pudimos recuperar la cuenta con esos datos.', code: 'recovery_failed' },
      { status: 400, statusText: 'Bad Request' });
    await settle(fixture);
    expect(el.querySelector('[role=alert]')?.textContent).toContain('No pudimos recuperar la cuenta con esos datos.');
  });
});
