import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { Auth } from '../../../core/services/auth';
import { Login } from './login';

function setup(loginWithPasskey: () => Promise<unknown>) {
  Object.defineProperty(window, 'PublicKeyCredential', { configurable: true, value: function PublicKeyCredential() {} });
  Object.defineProperty(navigator, 'credentials', { configurable: true, value: { get: vi.fn() } });
  TestBed.configureTestingModule({
    imports: [Login],
    providers: [provideRouter([]), { provide: Auth, useValue: { loginWithPasskey: vi.fn(loginWithPasskey), login: vi.fn() } }],
  });
  const fixture = TestBed.createComponent(Login);
  fixture.detectChanges();
  return { fixture, el: fixture.nativeElement as HTMLElement, auth: TestBed.inject(Auth) as unknown as { loginWithPasskey: ReturnType<typeof vi.fn> } };
}

describe('Login with a passkey (L15)', () => {
  it('offers the passkey button next to the password form and keeps the password path', () => {
    const { el } = setup(() => Promise.resolve());
    expect(el.querySelector('.passkey-login')?.textContent).toContain('Entrar con passkey');
    expect(el.querySelector('input[name="password"]')).not.toBeNull();
    expect(el.textContent).toContain('Usa un código de recuperación');
  });

  it('enters and follows the same safe returnUrl rule', async () => {
    const { fixture, el, auth } = setup(() => Promise.resolve());
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigateByUrl').mockResolvedValue(true);
    (el.querySelector('.passkey-login') as HTMLButtonElement).click();
    await fixture.whenStable();
    expect(auth.loginWithPasskey).toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith('/inicio');
  });

  it('explains a cancelled system dialog without blaming the account', async () => {
    const { fixture, el } = setup(() => Promise.reject(new DOMException('cancel', 'NotAllowedError')));
    (el.querySelector('.passkey-login') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(el.querySelector('[role="alert"]')?.textContent).toContain('No se usó ninguna passkey');
  });
});
