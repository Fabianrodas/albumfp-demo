import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { InstallPrompt } from '../../core/services/install-prompt';
import { WebApp } from './webapp';

describe('WebApp page', () => {
  function render() {
    TestBed.configureTestingModule({ imports: [WebApp], providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()] });
    const fixture = TestBed.createComponent(WebApp);
    fixture.detectChanges();
    return fixture;
  }

  it('explains the local install boundary and keeps notification claims accurate', () => {
    const el = render().nativeElement as HTMLElement;
    expect(el.textContent).toContain('Esta Demo solo funciona en este equipo');
    expect(el.textContent).toContain('no envía notificaciones push');
    expect(el.querySelectorAll('details').length).toBeGreaterThanOrEqual(5);
    expect(el.textContent).not.toContain('albumfp.com');
  });

  it('offers the install button only when the browser allows it', () => {
    const fixture = render();
    const installer = TestBed.inject(InstallPrompt);
    const actions = () => (fixture.nativeElement as HTMLElement).querySelector('.hero__actions') as HTMLElement;
    expect(actions().querySelector('button')).toBeNull();
    installer.canInstall.set(true);
    fixture.detectChanges();
    expect(actions().querySelector('button')?.textContent).toContain('Instalar AlbumFP');
    installer.installed.set(true);
    fixture.detectChanges();
    expect(actions().querySelector('button')).toBeNull();
    expect(actions().textContent).toContain('Ya estás usando AlbumFP como WebApp');
  });
});
