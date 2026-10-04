import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { Sidebar } from './sidebar';

describe('Sidebar (F01 information architecture)', () => {
  it('lists exactly the eight v1 destinations, in order', () => {
    TestBed.configureTestingModule({ imports: [Sidebar], providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()] });
    const fixture = TestBed.createComponent(Sidebar);
    fixture.detectChanges();
    const links = [...fixture.nativeElement.querySelectorAll('.primary-nav a')] as HTMLAnchorElement[];
    expect(links.map(a => a.textContent?.trim())).toEqual(
      ['Inicio', 'Biblioteca', 'Mis álbumes', 'Lugares', 'Favoritos', 'Compartido', 'Archivo', 'Papelera']);
    expect(fixture.nativeElement.textContent, 'logout moved to the account menu (F03)').not.toContain('Cerrar sesión');
    expect(links.map(a => a.getAttribute('href'))).toEqual(
      ['/inicio', '/biblioteca', '/albumes', '/lugares', '/favoritos', '/compartido', '/archivo', '/papelera']);
  });
});
