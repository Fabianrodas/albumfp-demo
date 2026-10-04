import { HttpEventType, provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { UploadPanel } from './upload-panel';

const photo = (name: string) => new File([name], name, { type: 'image/jpeg' });

describe('UploadPanel multi-upload', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [UploadPanel],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('keeps the secure single-file endpoint and starts at most three requests', () => {
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(UploadPanel);
    fixture.componentRef.setInput('albumId', 7);
    fixture.detectChanges();
    fixture.componentInstance.queue.add(['uno', 'dos', 'tres', 'cuatro'].map(name => ({
      file: photo(`${name}.jpg`), title: name,
    })));

    fixture.componentInstance.startUploads();
    const firstWave = http.match(request => request.method === 'POST' && request.url === '/api/albums/7/media');
    expect(firstWave.length).toBe(3);

    firstWave[0].event({ type: HttpEventType.UploadProgress, loaded: 5, total: 10 });
    expect(fixture.componentInstance.queue.items()[0].progress).toBe(50);
    firstWave[0].flush({ data: { id: 1, album_id: 7, file_type: 'image' }, message: 'ok' });

    const fourth = http.expectOne(request => request.method === 'POST' && request.url === '/api/albums/7/media');
    expect(fourth.request.body instanceof FormData).toBe(true);
    expect((fourth.request.body as FormData).get('caption')).toBeNull();
    fixture.destroy();
  });

  it('warns before closing and cancels files that have not started', () => {
    const fixture = TestBed.createComponent(UploadPanel);
    fixture.componentRef.setInput('albumId', 7);
    fixture.detectChanges();
    fixture.componentInstance.queue.add([{ file: photo('pendiente.jpg'), title: 'Pendiente' }]);
    let closes = 0;
    fixture.componentInstance.closed.subscribe(() => closes++);

    fixture.componentInstance.requestClose();
    expect(fixture.componentInstance.closeWarning()).toBe(true);
    expect(closes).toBe(0);

    fixture.componentInstance.cancelAndClose();
    expect(fixture.componentInstance.queue.items()[0].state).toBe('cancelled');
    expect(closes).toBe(1);
  });

  it('does not pretend to cancel a file already processing on the server', () => {
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(UploadPanel);
    fixture.componentRef.setInput('albumId', 7);
    fixture.detectChanges();
    fixture.componentInstance.queue.add([{ file: photo('procesando.jpg'), title: 'Procesando' }]);
    let closes = 0;
    fixture.componentInstance.closed.subscribe(() => closes++);
    fixture.componentInstance.startUploads();
    const request = http.expectOne('/api/albums/7/media');
    request.event({ type: HttpEventType.UploadProgress, loaded: 10, total: 10 });

    fixture.componentInstance.requestClose();
    fixture.componentInstance.cancelAndClose();

    expect(fixture.componentInstance.queue.items()[0].state).toBe('processing');
    expect(fixture.componentInstance.error()).toContain('servidor ya está procesando');
    expect(closes).toBe(0);
    fixture.destroy();
  });

  it('waits for local metadata and does not discard files with the same visible attributes', async () => {
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(UploadPanel);
    fixture.componentRef.setInput('albumId', 7);
    fixture.detectChanges();
    let finishMetadata!: (value: { resolution: string; duration: number }) => void;
    const pending = new Promise<{ resolution: string; duration: number }>(resolve => finishMetadata = resolve);
    vi.spyOn(fixture.componentInstance as any, 'readMediaMetadata').mockReturnValue(pending);
    const same = photo('mismo.jpg');

    fixture.componentInstance.addFiles([same, same]);
    expect(fixture.componentInstance.queue.items().length).toBe(2);
    fixture.componentInstance.startUploads();
    http.expectNone('/api/albums/7/media');
    expect(fixture.componentInstance.error()).toContain('metadatos');

    finishMetadata({ resolution: '1200x800', duration: 0 });
    await pending;
    await fixture.whenStable();
    fixture.componentInstance.startUploads();
    const requests = http.match('/api/albums/7/media');
    expect(requests.length).toBe(2);
    expect((requests[0].request.body as FormData).get('resolution')).toBe('1200x800');
    fixture.destroy();
  });

  it('stops a cancelled metadata read so it cannot block the remaining queue', () => {
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(UploadPanel);
    fixture.componentRef.setInput('albumId', 7);
    fixture.detectChanges();
    vi.spyOn(fixture.componentInstance as any, 'readMediaMetadata')
      .mockReturnValue(new Promise(() => {}));
    fixture.componentInstance.addFiles([photo('cancelar.jpg')]);
    const cancelled = fixture.componentInstance.queue.items()[0];

    fixture.componentInstance.cancel(cancelled);
    fixture.componentInstance.queue.add([{ file: photo('seguir.jpg'), title: 'Seguir' }]);
    fixture.componentInstance.startUploads();

    expect(fixture.componentInstance.metadataPendingCount()).toBe(0);
    http.expectOne('/api/albums/7/media');
    fixture.destroy();
  });

  it('times out a decoder that never reports load or error', async () => {
    vi.useFakeTimers();
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:stuck');
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    const fixture = TestBed.createComponent(UploadPanel);
    fixture.componentRef.setInput('albumId', 7);
    fixture.detectChanges();

    fixture.componentInstance.addFiles([photo('atascada.jpg')]);
    expect(fixture.componentInstance.metadataPendingCount()).toBe(1);
    await vi.advanceTimersByTimeAsync(10_000);

    expect(fixture.componentInstance.metadataPendingCount()).toBe(0);
    expect(revoke).toHaveBeenCalledWith('blob:stuck');
    fixture.destroy();
  });

  it('accepts HEIC and HEIF files even when the browser omits their MIME', async () => {
    const fixture = TestBed.createComponent(UploadPanel);
    fixture.componentRef.setInput('albumId', 7);
    fixture.detectChanges();
    vi.spyOn(fixture.componentInstance as any, 'readMediaMetadata')
      .mockResolvedValue({ resolution: null, duration: null });

    fixture.componentInstance.addFiles([
      new File(['heic'], 'iphone.heic', { type: '' }),
      new File(['heif'], 'camera.heif', { type: '' }),
    ]);
    await fixture.whenStable();

    expect(fixture.componentInstance.queue.items().map(item => item.file.name))
      .toEqual(['iphone.heic', 'camera.heif']);
    fixture.destroy();
  });

  it('offers a safe existing link and uploads again only after confirmation', () => {
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(UploadPanel);
    fixture.componentRef.setInput('albumId', 7);
    fixture.detectChanges();
    fixture.componentInstance.queue.add([{ file: photo('duplicada.jpg'), title: 'Duplicada' }]);
    fixture.componentInstance.startUploads();
    http.expectOne('/api/albums/7/media').flush({
      ok: false,
      code: 'exact_duplicate',
      message: 'Este archivo exacto ya existe',
      existing_media_id: 41,
      existing_album_id: 7,
    }, { status: 409, statusText: 'Conflict' });

    fixture.detectChanges();
    const link = fixture.nativeElement.querySelector('[data-testid="view-existing"]') as HTMLAnchorElement;
    const force = fixture.nativeElement.querySelector('[data-testid="force-duplicate"]') as HTMLButtonElement;
    expect(link.textContent).toContain('Ver existente');
    expect(link.getAttribute('href')).toBe('/albumes/7/media/41');
    expect(link.target).toBe('_blank');
    expect(force.textContent).toContain('Subir de todos modos');
    expect(fixture.nativeElement.textContent).not.toContain('Reintentar');
    // Ya está en este álbum: no hay nada que añadir.
    expect(fixture.nativeElement.querySelector('[data-testid="add-existing"]')).toBeNull();

    force.click();
    const repeated = http.expectOne('/api/albums/7/media');
    expect((repeated.request.body as FormData).get('force_duplicate')).toBe('true');
    repeated.flush({ data: { id: 42, album_id: 7, file_type: 'image' }, message: 'ok' });
    fixture.destroy();
  });

  function duplicateOf(existingAlbumId: number | null, canAddExisting: boolean) {
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(UploadPanel);
    fixture.componentRef.setInput('albumId', 7);
    fixture.componentRef.setInput('canAddExisting', canAddExisting);
    fixture.detectChanges();
    fixture.componentInstance.queue.add([{ file: photo('repetida.jpg'), title: 'Repetida' }]);
    fixture.componentInstance.startUploads();
    http.expectOne('/api/albums/7/media').flush({
      ok: false, code: 'exact_duplicate', message: 'Este archivo exacto ya existe en tu biblioteca',
      existing_media_id: 41, existing_album_id: existingAlbumId,
    }, { status: 409, statusText: 'Conflict' });
    fixture.detectChanges();
    return { http, fixture };
  }

  it('adds an existing duplicate to this album without uploading its bytes again', () => {
    const { http, fixture } = duplicateOf(null, true);
    const link = fixture.nativeElement.querySelector('[data-testid="view-existing"]') as HTMLAnchorElement;
    // Suelto (en ningún álbum): su detalle es la vista del recuerdo.
    expect(link.getAttribute('href')).toBe('/recuerdos/41');

    (fixture.nativeElement.querySelector('[data-testid="add-existing"]') as HTMLButtonElement).click();
    const put = http.expectOne('/api/albums/7/assets/41');
    expect(put.request.method).toBe('PUT');
    put.flush({ data: { album_id: 7, asset_id: 41, added: true }, message: 'ok' });
    fixture.detectChanges();

    expect(fixture.componentInstance.queue.items()[0].state).toBe('success');
    expect(fixture.nativeElement.querySelector('[data-testid="reused-existing"]')?.textContent).toContain('sin volver a subirlo');
    expect(http.match(request => request.method === 'POST').length).toBe(0);
    fixture.destroy();
  });

  it('never offers a collaborator to manage album membership', () => {
    const { fixture } = duplicateOf(5, false);
    expect(fixture.nativeElement.querySelector('[data-testid="add-existing"]')).toBeNull();
    expect((fixture.nativeElement.querySelector('[data-testid="view-existing"]') as HTMLAnchorElement)
      .getAttribute('href')).toBe('/albumes/5/media/41');
    fixture.destroy();
  });
});
