import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { Profile } from './profile';

const summary = (extra: object = {}) => ({
  data: { assets: 12, albums: 3, total_bytes: 5 * 1024 * 1024, largest_bytes: 1024, allowed: true, remaining: 3, retry_after: 100, ...extra },
  message: 'ok',
});

async function setup() {
  TestBed.configureTestingModule({ imports: [Profile], providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])] });
  const http = TestBed.inject(HttpTestingController);
  const fixture = TestBed.createComponent(Profile);
  fixture.detectChanges();
  // Lo que Perfil pide al abrirse no es el tema de este spec.
  http.match(() => true).forEach(r => r.flush({ data: r.request.url.endsWith('preferences')
    ? { notify_album_invites: true, notify_share_claimed: true, notify_shared_album_uploads: true } : [], message: 'ok' }));
  await settle(fixture);
  const profile = fixture.componentInstance;
  const opened = vi.spyOn(profile, 'openDownload').mockImplementation(() => {});
  return { http, fixture, profile, opened, el: fixture.nativeElement as HTMLElement };
}

async function settle(fixture: { detectChanges: () => void; whenStable: () => Promise<unknown> }) {
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
}

function checkbox(el: HTMLElement, label: string) {
  const row = [...el.querySelectorAll('.library-export label')].find(l => l.textContent?.includes(label));
  return row!.querySelector('input[type=checkbox]') as HTMLInputElement;
}

describe('Profile library export (L13)', () => {
  it('explains the copy and keeps every sensitive group off by default', async () => {
    const { el } = await setup();
    const section = el.querySelector('.library-export')!;
    expect(section.textContent).toContain('Exportar mi biblioteca');
    expect(section.textContent).toContain('mientras se descarga');
    for (const label of ['EXIF', 'Ubicación', 'Texto detectado']) {
      expect(checkbox(el, label).checked).toBe(false);
    }
  });

  it('checks the quota first and then downloads with exactly the chosen options', async () => {
    const { el, http, fixture, opened } = await setup();
    checkbox(el, 'Ubicación').click();
    checkbox(el, 'EXIF').click();
    await settle(fixture);
    (el.querySelector('.library-export button.btn-brand') as HTMLButtonElement).click();

    const req = http.expectOne(r => r.url === '/api/export/summary');
    expect(req.request.params.get('exif')).toBe('true');
    expect(req.request.params.get('location')).toBe('true');
    expect(req.request.params.get('ocr')).toBe('false');
    req.flush(summary());
    await settle(fixture);

    expect(opened).toHaveBeenCalledWith('/api/export/download?exif=true&location=true&ocr=false');
    expect(el.textContent).toContain('12 recuerdos');
  });

  it('does not start a download the server would refuse, and says when to retry', async () => {
    const { el, http, fixture, opened } = await setup();
    (el.querySelector('.library-export button.btn-brand') as HTMLButtonElement).click();
    http.expectOne(r => r.url === '/api/export/summary').flush(summary({ allowed: false, remaining: 0, retry_after: 1500 }));
    await settle(fixture);
    expect(opened).not.toHaveBeenCalled();
    expect(el.querySelector('.library-export')!.textContent).toContain('25 min');
  });
});
