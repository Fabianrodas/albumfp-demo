import { HttpEventType, provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { UploadPanel } from './upload-panel';

const photo = (name: string) => new File([name], name, { type: 'image/jpeg' });
const video = (name: string) => new File([name], name, { type: 'video/mp4' });
const URL_UPLOAD = '/api/albums/7/media';

describe('UploadPanel single upload (v1.1)', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [UploadPanel],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    });
  });

  afterEach(() => vi.restoreAllMocks());

  function setup(inputs: Record<string, unknown> = {}) {
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(UploadPanel);
    fixture.componentRef.setInput('albumId', 7);
    for (const [key, value] of Object.entries(inputs)) fixture.componentRef.setInput(key, value);
    fixture.detectChanges();
    const panel = fixture.componentInstance;
    // jsdom no decodifica imágenes ni videos: los metadatos locales se simulan.
    vi.spyOn(panel as any, 'readMetadata').mockResolvedValue(undefined);
    const events = { uploaded: 0, closed: 0 };
    panel.uploaded.subscribe(() => events.uploaded++);
    panel.closed.subscribe(() => events.closed++);
    return { http, fixture, panel, events };
  }

  async function start(panel: UploadPanel, file: File, http: HttpTestingController) {
    panel.selectFile(file);
    const submitted = panel.submit();
    await submitted;
    return http.expectOne(request => request.method === 'POST' && request.url === URL_UPLOAD);
  }

  it('uploads ONE file through the secure endpoint and closes itself on success', async () => {
    const { http, panel, events } = setup();
    const request = await start(panel, photo('playa.jpg'), http);
    expect(request.request.body instanceof FormData).toBe(true);
    expect((request.request.body as FormData).get('title')).toBe('playa');
    expect(panel.phase()).toBe('preparing');

    request.flush({ data: { id: 1, file_type: 'image' }, message: 'ok' });
    await Promise.resolve();
    expect(panel.phase()).toBe('done');
    expect(events).toEqual({ uploaded: 1, closed: 1 });
    http.verify();
  });

  it('reports real byte progress: preparing, uploading %, then finalizing', async () => {
    const { http, panel } = setup();
    const request = await start(panel, photo('grande.jpg'), http);
    expect(panel.phase()).toBe('preparing');
    expect(panel.percent()).toBe(0);

    request.event({ type: HttpEventType.UploadProgress, loaded: 25, total: 100 });
    expect(panel.phase()).toBe('uploading');
    expect(panel.percent()).toBe(25);
    request.event({ type: HttpEventType.UploadProgress, loaded: 99, total: 100 });
    expect(panel.percent()).toBe(99);
    request.event({ type: HttpEventType.UploadProgress, loaded: 100, total: 100 });
    expect(panel.phase()).toBe('finalizing');
    request.flush({ data: { id: 2, file_type: 'image' }, message: 'ok' });
  });

  it('cancels only while bytes are still leaving, and never while the server finalizes', async () => {
    const { http, panel, events } = setup();
    let request = await start(panel, photo('uno.jpg'), http);
    request.event({ type: HttpEventType.UploadProgress, loaded: 10, total: 100 });
    panel.cancelUpload();
    expect(request.cancelled).toBe(true);
    expect(panel.phase()).toBe('ready');

    const again = panel.submit();
    await again;
    request = http.expectOne(URL_UPLOAD);
    request.event({ type: HttpEventType.UploadProgress, loaded: 100, total: 100 });
    panel.cancelUpload();
    panel.requestClose();
    expect(request.cancelled).toBe(false);
    expect(panel.phase()).toBe('finalizing');
    expect(events.closed).toBe(0);
    expect(panel.error()).toContain('guardando');
    request.flush({ data: { id: 3, file_type: 'image' }, message: 'ok' });
  });

  it('warns before closing an upload in progress', async () => {
    const { http, panel, events } = setup();
    const request = await start(panel, photo('dos.jpg'), http);
    panel.requestClose();
    expect(panel.closeWarning()).toBe(true);
    expect(events.closed).toBe(0);
    panel.cancelAndClose();
    expect(request.cancelled).toBe(true);
    expect(events.closed).toBe(1);
  });

  it('never retries blindly after an ambiguous failure, but does after a clear 4xx', async () => {
    const { http, fixture, panel } = setup();
    let request = await start(panel, photo('tres.jpg'), http);
    request.flush({ message: 'boom' }, { status: 502, statusText: 'Bad Gateway' });
    fixture.detectChanges();
    expect(panel.ambiguous()).toBe(true);
    expect(fixture.nativeElement.textContent).toContain('Revisa el álbum');
    expect(fixture.nativeElement.textContent).not.toContain('Reintentar');

    panel.selectFile(photo('cuatro.jpg'));
    await panel.submit();
    request = http.expectOne(URL_UPLOAD);
    request.flush({ message: 'Formato no admitido' }, { status: 400, statusText: 'Bad Request' });
    fixture.detectChanges();
    expect(panel.ambiguous()).toBe(false);
    expect(fixture.nativeElement.textContent).toContain('Reintentar');
  });

  it('accepts HEIC/HEIF without a MIME and uses only the first dropped file', () => {
    const { panel } = setup();
    panel.selectFile(new File(['heic'], 'iphone.heic', { type: '' }));
    expect(panel.file()?.name).toBe('iphone.heic');
    const drop = { preventDefault() {}, dataTransfer: { files: [photo('a.jpg'), photo('b.jpg')] } } as unknown as DragEvent;
    panel.onDrop(drop);
    expect(panel.file()?.name).toBe('a.jpg');
    expect(panel.error()).toContain('un archivo a la vez');
  });

  it('saves the chosen frame as the poster right after a video upload', async () => {
    const { http, panel, events } = setup();
    const request = await start(panel, video('olas.mp4'), http);
    (panel as any).posterBlob = new Blob(['frame'], { type: 'image/jpeg' });
    request.flush({ data: { id: 9, file_type: 'video' }, message: 'ok' });
    await Promise.resolve();
    const poster = http.expectOne('/api/media/9/poster');
    expect(poster.request.method).toBe('PUT');
    expect((poster.request.body as FormData).get('poster')).toBeTruthy();
    poster.flush({ data: { media_id: 9 }, message: 'ok' });
    await new Promise(resolve => setTimeout(resolve));
    expect(events).toEqual({ uploaded: 1, closed: 1 });
  });

  it('offers a safe existing link and uploads again only after confirmation', async () => {
    const { http, fixture, panel } = setup();
    const request = await start(panel, photo('duplicada.jpg'), http);
    request.flush({ ok: false, code: 'exact_duplicate', message: 'Este archivo exacto ya existe',
      existing_media_id: 41, existing_album_id: 7 }, { status: 409, statusText: 'Conflict' });
    fixture.detectChanges();
    const link = fixture.nativeElement.querySelector('[data-testid="view-existing"]') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('/albumes/7/media/41');
    expect(link.target).toBe('_blank');
    expect(fixture.nativeElement.querySelector('[data-testid="add-existing"]')).toBeNull();

    (fixture.nativeElement.querySelector('[data-testid="force-duplicate"]') as HTMLButtonElement).click();
    await new Promise(resolve => setTimeout(resolve));
    const repeated = http.expectOne(URL_UPLOAD);
    expect((repeated.request.body as FormData).get('force_duplicate')).toBe('true');
    repeated.flush({ data: { id: 42, file_type: 'image' }, message: 'ok' });
  });

  it('adds an existing duplicate to this album without uploading its bytes again', async () => {
    const { http, fixture, panel } = setup({ canAddExisting: true });
    const request = await start(panel, photo('repetida.jpg'), http);
    request.flush({ ok: false, code: 'exact_duplicate', message: 'Ya existe',
      existing_media_id: 41, existing_album_id: null }, { status: 409, statusText: 'Conflict' });
    fixture.detectChanges();
    expect((fixture.nativeElement.querySelector('[data-testid="view-existing"]') as HTMLAnchorElement)
      .getAttribute('href')).toBe('/recuerdos/41');
    (fixture.nativeElement.querySelector('[data-testid="add-existing"]') as HTMLButtonElement).click();
    const put = http.expectOne('/api/albums/7/assets/41');
    expect(put.request.method).toBe('PUT');
    put.flush({ data: { album_id: 7, asset_id: 41, added: true }, message: 'ok' });
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[data-testid="reused-existing"]')?.textContent).toContain('sin volver a subirlo');
    expect(http.match(r => r.method === 'POST').length).toBe(0);
  });

  it('never offers a collaborator to manage album membership', async () => {
    const { http, fixture, panel } = setup({ canAddExisting: false });
    const request = await start(panel, photo('otra.jpg'), http);
    request.flush({ ok: false, code: 'exact_duplicate', message: 'Ya existe',
      existing_media_id: 41, existing_album_id: 5 }, { status: 409, statusText: 'Conflict' });
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[data-testid="add-existing"]')).toBeNull();
  });
});
