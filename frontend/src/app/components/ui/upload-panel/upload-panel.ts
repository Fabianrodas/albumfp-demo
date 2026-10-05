import { DecimalPipe } from '@angular/common';
import { HttpErrorResponse, HttpEventType } from '@angular/common/http';
import { Component, OnDestroy, computed, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Subscription, firstValueFrom } from 'rxjs';
import { Album, AlbumApi, Media } from '../../../core/services/album-api';
import { mediaDetailLink } from '../../../core/utils/media-navigation';
import { DialogTrap } from '../../../core/directives/dialog-trap';
import { pagedList } from '../../../core/utils/paged-list';
import { captureFrame, defaultPosterTime, knownDuration } from '../../../core/utils/video-frame';
import { Icon } from '../icon/icon';
import { LocationPicker } from '../location-picker/location-picker';

/**
 * v1.1: UNA subida lineal. Un archivo, un formulario, un ciclo de vida:
 *
 *   ready → preparing → uploading (bytes reales) → finalizing → done (cierra)
 *                                                            ↘ error
 *
 * `preparing` dura hasta el PRIMER evento de bytes del navegador: si la
 * petición todavía no empezó a subir (cola del navegador, conexión), la barra
 * no se queda en un «Subiendo 0%» que miente. `finalizing` empieza cuando el
 * último byte salió y dura mientras el servidor valida, guarda y (en
 * producción) copia el original al origen privado; ahí ya no se puede
 * cancelar, porque el servidor puede haber confirmado.
 *
 * El progreso sale de los eventos `UploadProgress` del XHR: nunca de un
 * temporizador.
 */
export type UploadPhase = 'ready' | 'preparing' | 'uploading' | 'finalizing' | 'done' | 'error';

const ACCEPTED_EXTENSIONS = new Set([
  'jpg', 'jpeg', 'png', 'webp', 'gif', 'avif', 'heic', 'heif',
  'mp4', 'webm', 'ogg', 'mov', 'mkv', 'm4v',
]);

interface Duplicate { mediaId: number; albumId: number | null; inTarget: boolean }

@Component({
  selector: 'app-upload-panel',
  imports: [DecimalPipe, FormsModule, RouterLink, Icon, DialogTrap, LocationPicker],
  templateUrl: './upload-panel.html',
  styleUrl: './upload-panel.css',
})
export class UploadPanel implements OnDestroy {
  private readonly api = inject(AlbumApi);

  albumId = input<number | null>(null);
  allowFavorite = input(false);
  /** El dueño del álbum puede meter en él un duplicado que ya tenía, sin
   * volver a subirlo (L10B). Un colaborador no gestiona pertenencias. */
  canAddExisting = input(false);
  closed = output<void>();
  uploaded = output<Media>();
  close = () => this.requestClose();

  private readonly albumPages = pagedList<Album>((page, perPage) =>
    this.api.albums({ role: 'owner', page: String(page), per_page: String(perPage) }));
  readonly albums = this.albumPages.items;
  readonly detailLink = mediaDetailLink;

  readonly file = signal<File | null>(null);
  readonly phase = signal<UploadPhase>('ready');
  readonly loaded = signal(0);
  readonly total = signal(0);
  readonly percent = computed(() => this.total() ? Math.min(100, Math.floor(100 * this.loaded() / this.total())) : 0);
  readonly error = signal('');
  /** Tras un corte o un 5xx el servidor pudo haber guardado igual: no se
   * ofrece reintentar a ciegas (crearía un duplicado). */
  readonly ambiguous = signal(false);
  readonly duplicate = signal<Duplicate | null>(null);
  readonly reused = signal(false);
  readonly closeWarning = signal(false);
  readonly dragging = signal(false);
  readonly locationOpen = signal(false);
  readonly busy = computed(() => ['preparing', 'uploading', 'finalizing'].includes(this.phase()));
  readonly isVideo = computed(() => this.isVideoFile(this.file()));

  // Portada de video: se elige un momento al azar al cargar el archivo y el
  // control deslizante permite escoger otro.
  readonly videoUrl = signal('');
  readonly duration = signal(0);
  readonly posterTime = signal(0);
  readonly posterUrl = signal('');
  readonly posterFailed = signal(false);
  private posterBlob: Blob | null = null;
  private posterVideo?: HTMLVideoElement;
  private posterSeq = 0;

  title = '';
  caption = '';
  takenAt = '';
  favorite = false;
  useLocation = false;
  manualLatitude: number | null = null;
  manualLongitude: number | null = null;
  selectedAlbumId: number | null = null;
  private resolution: string | null = null;
  private metadataReady: Promise<void> = Promise.resolve();
  private request?: Subscription;

  constructor() {
    effect(() => {
      const first = this.albums();
      if (first.length && this.selectedAlbumId === null) this.selectedAlbumId = first[0].id;
    });
  }

  ngOnInit() {
    if (!this.albumId()) {
      this.albumPages.loadAll();
      if (this.albumPages.error()) this.error.set('No se pudieron cargar tus álbumes.');
    }
  }

  ngOnDestroy() {
    this.request?.unsubscribe();
    this.releaseFile();
  }

  onFileInput(event: Event) {
    const input = event.target as HTMLInputElement;
    const picked = input.files?.[0];
    input.value = '';
    if (picked) this.selectFile(picked);
  }

  onDrop(event: DragEvent) {
    event.preventDefault();
    this.dragging.set(false);
    const files = Array.from(event.dataTransfer?.files || []);
    if (files[0]) this.selectFile(files[0]);
    if (files.length > 1) this.error.set('Sube un archivo a la vez: se usó el primero.');
  }

  selectFile(file: File) {
    if (this.busy()) return;
    if (!this.isAccepted(file)) { this.error.set('Ese archivo no es una foto o video compatible.'); return; }
    this.releaseFile();
    this.error.set('');
    this.ambiguous.set(false);
    this.duplicate.set(null);
    this.reused.set(false);
    this.phase.set('ready');
    this.file.set(file);
    this.title = file.name.replace(/\.[^.]+$/, '').slice(0, 120);
    this.metadataReady = this.readMetadata(file);
  }

  clearFile() {
    if (this.busy()) return;
    this.releaseFile();
    this.file.set(null);
    this.phase.set('ready');
    this.error.set('');
    this.duplicate.set(null);
  }

  onLocationPicked(point: { latitude: number; longitude: number }) {
    this.manualLatitude = point.latitude;
    this.manualLongitude = point.longitude;
  }

  /** El control deslizante de la portada: captura el fotograma de ese momento. */
  choosePosterTime(time: number) {
    this.posterTime.set(time);
    void this.capturePoster(time);
  }

  async submit(forceDuplicate = false) {
    const file = this.file();
    const albumId = this.albumId() ?? this.selectedAlbumId;
    if (!file || this.busy()) return;
    if (!albumId) { this.error.set('Selecciona un álbum antes de subir.'); return; }
    if (this.useLocation && (this.manualLatitude == null || this.manualLongitude == null)) {
      this.error.set('Elige un punto en el mapa o desactiva el lugar.');
      return;
    }
    this.error.set('');
    this.ambiguous.set(false);
    this.duplicate.set(null);
    this.closeWarning.set(false);
    this.loaded.set(0);
    this.total.set(file.size);
    this.phase.set('preparing');
    await this.metadataReady;

    this.request = this.api.createMediaWithProgress(albumId, {
      file,
      title: this.title.trim() || null,
      caption: this.caption.trim() || null,
      taken_at: this.takenAt || null,
      is_favorite: this.allowFavorite() && this.favorite,
      force_duplicate: forceDuplicate || undefined,
      resolution: this.resolution,
      duration: this.duration() ? Math.round(this.duration()) : null,
      latitude: this.useLocation ? this.manualLatitude : null,
      longitude: this.useLocation ? this.manualLongitude : null,
    }).subscribe({
      next: event => {
        if (event.type === HttpEventType.UploadProgress) {
          this.loaded.set(event.loaded);
          if (event.total) this.total.set(event.total);
          this.phase.set(event.total && event.loaded >= event.total ? 'finalizing' : 'uploading');
        } else if (event.type === HttpEventType.Response) {
          void this.finish(event.body!.data);
        }
      },
      error: (error: HttpErrorResponse) => this.fail(error),
    });
  }

  /** Cancelar solo mientras los bytes siguen saliendo: después, el servidor
   * pudo haber confirmado y cancelar sería mentir. */
  cancelUpload() {
    if (!['preparing', 'uploading'].includes(this.phase())) return;
    this.request?.unsubscribe();
    this.request = undefined;
    this.phase.set('ready');
    this.loaded.set(0);
    this.error.set('Subida cancelada. Puedes volver a intentarlo.');
  }

  retry() { void this.submit(); }
  forceDuplicate() { void this.submit(true); }

  addExisting() {
    const duplicate = this.duplicate();
    const albumId = this.albumId() ?? this.selectedAlbumId;
    if (!duplicate || duplicate.inTarget || !albumId || !this.canAddExisting()) return;
    this.error.set('');
    this.api.addToAlbum(albumId, duplicate.mediaId).subscribe({
      next: () => {
        this.reused.set(true);
        this.duplicate.set(null);
        this.phase.set('done');
        window.setTimeout(() => this.closed.emit(), 900);
      },
      error: error => this.error.set(error?.error?.message || 'No se pudo añadir el recuerdo existente.'),
    });
  }

  requestClose() {
    if (this.phase() === 'finalizing') {
      this.error.set('El servidor está guardando el archivo. Espera a que termine.');
      return;
    }
    if (this.busy()) { this.closeWarning.set(true); return; }
    this.closed.emit();
  }

  cancelAndClose() {
    if (this.phase() === 'finalizing') return;
    this.request?.unsubscribe();
    this.closed.emit();
  }

  phaseLabel() {
    return ({
      ready: 'Listo para subir', preparing: 'Preparando…', uploading: 'Subiendo',
      finalizing: 'Finalizando…', done: 'Completado', error: 'Error',
    } satisfies Record<UploadPhase, string>)[this.phase()];
  }

  sizeLabel(bytes: number) {
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  private async finish(media: Media) {
    this.request = undefined;
    // La portada va aparte y después: si falla, el video ya está guardado y
    // se puede elegir otra desde su detalle. No se bloquea el cierre por ella.
    if (media.file_type === 'video') {
      const poster = this.posterBlob ?? await this.capturePoster(this.posterTime()).catch(() => null);
      if (poster) await firstValueFrom(this.api.setVideoPoster(media.id, poster)).catch(() => undefined);
    }
    this.phase.set('done');
    this.uploaded.emit(media);
    this.closed.emit();
  }

  private fail(error: HttpErrorResponse) {
    this.request = undefined;
    this.phase.set('error');
    const body = error?.error ?? {};
    if (body.code === 'exact_duplicate') {
      const mediaId = Number(body.existing_media_id);
      const raw = Number(body.existing_album_id);
      const albumId = Number.isSafeInteger(raw) && raw > 0 ? raw : null;
      const target = this.albumId() ?? this.selectedAlbumId;
      if (Number.isSafeInteger(mediaId) && mediaId > 0) {
        this.duplicate.set({ mediaId, albumId, inTarget: albumId !== null && albumId === target });
      }
      this.error.set(body.message || 'Este archivo exacto ya existe.');
      return;
    }
    const status = error?.status ?? 0;
    this.ambiguous.set(status === 0 || status >= 500);
    this.error.set(this.ambiguous()
      ? 'Se perdió la conexión antes de saber si el archivo se guardó. Revisa el álbum antes de volver a subirlo.'
      : body.message || 'No se pudo subir el archivo.');
  }

  private isAccepted(file: File) {
    if (file.type.startsWith('image/') || file.type.startsWith('video/')) return true;
    const extension = file.name.split('.').pop()?.toLowerCase() || '';
    return !file.type && ACCEPTED_EXTENSIONS.has(extension);
  }

  private isVideoFile(file: File | null) {
    if (!file) return false;
    if (file.type) return file.type.startsWith('video/');
    return ['mp4', 'webm', 'ogg', 'mov', 'mkv', 'm4v'].includes(file.name.split('.').pop()?.toLowerCase() || '');
  }

  private releaseFile() {
    this.posterSeq++;
    this.posterVideo?.removeAttribute('src');
    this.posterVideo = undefined;
    for (const url of [this.videoUrl(), this.posterUrl()]) if (url) URL.revokeObjectURL(url);
    this.videoUrl.set('');
    this.posterUrl.set('');
    this.posterBlob = null;
    this.posterFailed.set(false);
    this.duration.set(0);
    this.resolution = null;
  }

  /** Resolución y duración locales (el servidor valida lo suyo), y para un
   * video su primera portada. Nunca bloquea más de 10 s. */
  private readMetadata(file: File): Promise<void> {
    const url = URL.createObjectURL(file);
    if (!this.isVideoFile(file)) {
      return new Promise<void>(resolve => {
        const image = new Image();
        const done = () => { URL.revokeObjectURL(url); resolve(); };
        const timer = window.setTimeout(done, 10_000);
        image.onload = () => { window.clearTimeout(timer); this.resolution = `${image.naturalWidth}x${image.naturalHeight}`; done(); };
        image.onerror = () => { window.clearTimeout(timer); done(); };
        image.src = url;
      });
    }
    this.videoUrl.set(url);
    const video = document.createElement('video');
    video.muted = true;
    video.preload = 'auto';
    video.playsInline = true;
    this.posterVideo = video;
    return new Promise<void>(resolve => {
      const timer = window.setTimeout(() => { this.posterFailed.set(true); resolve(); }, 10_000);
      video.onloadedmetadata = async () => {
        window.clearTimeout(timer);
        video.onloadedmetadata = null;
        if (video.videoWidth && video.videoHeight) this.resolution = `${video.videoWidth}x${video.videoHeight}`;
        const duration = await knownDuration(video);
        this.duration.set(duration);
        const time = defaultPosterTime(duration);
        this.posterTime.set(time);
        void this.capturePoster(time).finally(resolve);
      };
      video.onerror = () => { window.clearTimeout(timer); this.posterFailed.set(true); resolve(); };
      video.src = url;
    });
  }

  private async capturePoster(time: number): Promise<Blob | null> {
    const video = this.posterVideo;
    if (!video) return null;
    const seq = ++this.posterSeq;
    try {
      const blob = await captureFrame(video, time);
      if (seq !== this.posterSeq) return blob;
      if (this.posterUrl()) URL.revokeObjectURL(this.posterUrl());
      this.posterBlob = blob;
      this.posterUrl.set(URL.createObjectURL(blob));
      this.posterFailed.set(false);
      return blob;
    } catch {
      if (seq === this.posterSeq) this.posterFailed.set(true);
      return null;
    }
  }
}
