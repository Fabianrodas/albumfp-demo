import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { routes } from './app.routes';

@Component({ template: '' })
class Blank {}

/** F01: las rutas viejas redirigen a la vista canónica. Se prueba la tabla de
 * rutas REAL (sin los guards, que necesitan sesión): solo importa a dónde
 * lleva cada URL. */
describe('F01 route table', () => {
  const dashboard = routes.find(r => r.children?.some(c => c.path === 'inicio'))!;
  const children = dashboard.children!.map(c =>
    c.loadComponent ? { path: c.path, component: Blank } : c);

  async function land(url: string) {
    TestBed.configureTestingModule({ providers: [provideRouter([{ path: '', children }])] });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    return TestBed.inject(Router).url;
  }

  it('sends /explorar to Inicio, where Explore now lives', async () => {
    expect(await land('/explorar')).toBe('/inicio');
  });

  it('sends /usuarios to Inicio with the people panel open', async () => {
    expect(await land('/usuarios')).toBe('/inicio?panel=personas');
  });

  it('keeps no standalone Explore/Users page in the authenticated table', () => {
    const paths = dashboard.children!.filter(c => c.loadComponent).map(c => c.path);
    expect(paths).not.toContain('explorar');
    expect(paths).not.toContain('usuarios');
    expect(paths).toEqual(expect.arrayContaining(
      ['inicio', 'biblioteca', 'albumes', 'lugares', 'favoritos', 'compartido', 'archivo', 'papelera', 'perfil']));
  });
});
