import { Type } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { MobileNav } from './mobile-nav';
import { Sidebar } from '../sidebar/sidebar';

describe('MobileNav (phone navigation)', () => {
  function render<T>(cmp: Type<T>) {
    TestBed.configureTestingModule({ imports: [cmp], providers: [provideRouter([])] });
    const fixture = TestBed.createComponent(cmp);
    fixture.detectChanges();
    return fixture;
  }

  it('reaches exactly the eight sidebar destinations: four tabs plus «Más»', () => {
    const nav = render(MobileNav);
    const tabs = [...nav.nativeElement.querySelectorAll('.tabbar a')].map((a: Element) => a.getAttribute('href'));
    expect(tabs).toEqual(['/inicio', '/biblioteca', '/albumes', '/compartido']);

    (nav.nativeElement.querySelector('.tabbar button') as HTMLButtonElement).click();
    nav.detectChanges();
    const more = [...nav.nativeElement.querySelectorAll('.sheet a')].map((a: Element) => a.getAttribute('href'));
    expect(more).toEqual(['/lugares', '/favoritos', '/archivo', '/papelera']);

    TestBed.resetTestingModule();
    const sidebar = render(Sidebar);
    const desktop = [...sidebar.nativeElement.querySelectorAll('.primary-nav a')].map((a: Element) => a.getAttribute('href'));
    expect([...tabs, ...more].sort()).toEqual([...desktop].sort());
  });

  it('keeps the theme toggle reachable on the phone and closes the sheet', () => {
    const nav = render(MobileNav);
    const button = nav.nativeElement.querySelector('.tabbar button') as HTMLButtonElement;
    button.click();
    nav.detectChanges();
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(nav.nativeElement.querySelector('.sheet__theme')).not.toBeNull();

    (nav.nativeElement.querySelector('.sheet__close') as HTMLButtonElement).click();
    nav.detectChanges();
    expect(nav.nativeElement.querySelector('.sheet')).toBeNull();
    expect(button.getAttribute('aria-expanded')).toBe('false');
  });
});
