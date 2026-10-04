import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { Router, provideRouter } from '@angular/router';
import { Topbar } from '../../layout/topbar/topbar';
import { Auth } from '../../../core/services/auth';
import { of } from 'rxjs';

/** F03: el menú de la cuenta del panel, probado a través de la topbar real. */
describe('Account menu (F03)', () => {
  function setup() {
    TestBed.configureTestingModule({ imports: [Topbar], providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()] });
    const auth = TestBed.inject(Auth);
    auth.user.set({ id: 9, username: 'ana', full_name: 'Ana', has_avatar: false });
    const fixture = TestBed.createComponent(Topbar);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const trigger = el.querySelector('.user-menu__trigger') as HTMLButtonElement;
    return { fixture, el, trigger, auth };
  }
  const items = (el: HTMLElement) => [...el.querySelectorAll('[role="menuitem"]')] as HTMLButtonElement[];
  const tick = () => new Promise(resolve => setTimeout(resolve));

  it('orders Mi perfil, Ir al sitio público, separator, Cerrar sesión (destructive)', () => {
    const { fixture, el, trigger } = setup();
    trigger.click();
    fixture.detectChanges();
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    expect(items(el).map(b => b.textContent?.trim())).toEqual(['Mi perfil', 'Ir al sitio público', 'Cerrar sesión']);
    const panel = el.querySelector('[role="menu"]')!;
    const children = [...panel.children];
    expect(children[2].getAttribute('role')).toBe('separator');
    expect(items(el)[2].classList).toContain('is-danger');
  });

  it('is keyboard operable: focus moves in, arrows wrap, Escape returns focus to the avatar', async () => {
    const { fixture, el, trigger } = setup();
    trigger.click();
    fixture.detectChanges();
    await tick();
    expect(document.activeElement).toBe(items(el)[0]);
    const panel = el.querySelector('[role="menu"]') as HTMLElement;
    panel.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    expect(document.activeElement).toBe(items(el)[2]);
    panel.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    expect(document.activeElement).toBe(items(el)[0]);
    panel.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
    expect(document.activeElement).toBe(items(el)[2]);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    fixture.detectChanges();
    expect(el.querySelector('[role="menu"]')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('closes on an outside click', () => {
    const { fixture, el, trigger } = setup();
    trigger.click();
    fixture.detectChanges();
    document.body.click();
    fixture.detectChanges();
    expect(el.querySelector('[role="menu"]')).toBeNull();
  });

  it('logs out through the server and only then leaves', () => {
    const { fixture, el, trigger, auth } = setup();
    const logout = vi.spyOn(auth, 'logout').mockReturnValue(of(undefined) as never);
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigateByUrl').mockResolvedValue(true);
    trigger.click();
    fixture.detectChanges();
    items(el)[2].click();
    expect(logout).toHaveBeenCalledOnce();
    expect(navigate).toHaveBeenCalledWith('/');
  });
});
