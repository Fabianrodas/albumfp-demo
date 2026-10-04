import { HttpEventType } from '@angular/common/http';
import { Component, OnDestroy, computed, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Observable } from 'rxjs';
import { Album, AlbumApi, Media, MediaUploadPayload } from '../../../core/services/album-api';
import { mediaDetailLink } from '../../../core/utils/media-navigation';
import { DialogTrap } from '../../../core/directives/dialog-trap';
import { pagedList } from '../../../core/utils/paged-list';
import {
  UploadQueue,
  UploadQueueEvent,
  UploadQueueItem,
  UploadQueueState,
} from '../../../core/utils/upload-queue';
import { Icon } from '../icon/icon';
import { LocationPicker } from '../location-picker/location-picker';

interface UploadRequest {
  albumId: number;
  payload: MediaUploadPayload;
}

type QueueItem = UploadQueueItem<UploadRequest, Media>;

const ACCEPTED_EXTENSIONS = new Set([
  'jpg', 'jpeg', 'png', 'webp', 'gif', 'avif', 'heic', 'heif',
  'mp4', 'webm', 'ogg', 'mov', 'mkv', 'm4v',
]);

@Component({
  selector: 'app-upload-panel',
  imports: [FormsModule, RouterLink, Icon, DialogTrap, LocationPicker],
  templateUrl: './upload-panel.html',
  styleUrl: './upload-panel.css',
})
export class UploadPanel implements OnDestroy {
  private readonly api = inject(AlbumApi);
  private readonly metadataReaders = new Map<number, () => void>();

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

  readonly queue = new UploadQueue<UploadRequest, Media>(item => this.uploadItem(item), 3);
  readonly failedCount = computed(() => this.queue.items().filter(item => item.state === 'failed').length);
  readonly cancelledCount = computed(() => this.queue.items().filter(item => item.state === 'cancelled').length);
  readonly queuedCount = computed(() => this.queue.items().filter(item => item.state === 'queued').length);
  readonly completedCount = computed(() => this.queue.items().filter(item =>
    ['success', 'failed', 'cancelled'].includes(item.state)).length);
  readonly metadataPending = signal<ReadonlySet<number>>(new Set<number>());
  /** Duplicados resueltos añadiendo el recuerdo existente, sin subir bytes. */
  readonly reused = signal<ReadonlySet<number>>(new Set<number>());
  readonly detailLink = mediaDetailLink;
  readonly metadataPendingCount = computed(() => this.metadataPending().size);

  dragging = signal(false);
  error = signal('');
  closeWarning = signal(false);
  locationOpen = signal(false);

  selectedAlbumId: number | null = null;
  applyCaption = false;
  sharedCaption = '';
  applyTakenAt = false;
  sharedTakenAt = '';
  applyFavorite = false;
  sharedFavorite = false;
  applyLocation = false;
  manualLatitude: number | null = null;
  manualLongitude: number | null = null;

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
    this.queue.cancelAllCancelable();
    for (const cancel of [...this.metadataReaders.values()]) cancel();
  }

  onFileInput(event: Event) {
    const input = event.target as HTMLInputElement;
    this.addFiles(Array.from(input.files || []));
    input.value = '';
  }

  onDrop(event: DragEvent) {
    event.preventDefault();
    this.dragging.set(false);
    this.addFiles(Array.from(event.dataTransfer?.files || []));
  }

  addFiles(files: File[]) {
    if (this.queue.hasActive()) {
      this.error.set('Espera a que terminen las subidas activas para añadir más archivos.');
      return;
    }
    this.error.set('');
    const accepted: File[] = [];
    let rejected = 0;

    for (const file of files) {
      if (!this.isAccepted(file)) { rejected++; continue; }
      accepted.push(file);
    }

    const ids = this.queue.add(accepted.map(file => ({
      file,
      title: file.name.replace(/\.[^.]+$/, '').slice(0, 120),
      resolution: null,
      duration: null,
    })));
    this.metadataPending.update(current => new Set([...current, ...ids]));
    accepted.forEach((file, index) => void this.readMediaMetadata(file, ids[index])
      .then(metadata => this.queue.updateMetadata(ids[index], metadata))
      .finally(() => this.metadataPending.update(current => {
        const next = new Set(current);
        next.delete(ids[index]);
        return next;
      })));

    const notices: string[] = [];
    if (rejected) notices.push(`${rejected} archivo${rejected === 1 ? '' : 's'} no compatible${rejected === 1 ? '' : 's'}`);
    if (notices.length) this.error.set(`${notices.join(' y ')}.`);
  }

  updateTitle(item: QueueItem, title: string) {
    this.queue.updateTitle(item.id, title);
  }

  onLocationPicked(point: { latitude: number; longitude: number }) {
    this.manualLatitude = point.latitude;
    this.manualLongitude = point.longitude;
  }

  startUploads() {
    this.error.set('');
    const targetAlbumId = this.albumId() ?? this.selectedAlbumId;
    if (!targetAlbumId) { this.error.set('Selecciona un álbum antes de subir.'); return; }
    if (!this.queuedCount()) { this.error.set('Selecciona al menos una foto o video.'); return; }
    if (this.metadataPendingCount()) {
      this.error.set('Espera a que termine la lectura de metadatos de los archivos.');
      return;
    }
    if (this.applyLocation && (this.manualLatitude == null || this.manualLongitude == null)) {
      this.error.set('Elige un punto en el mapa o desactiva el lugar compartido.');
      return;
    }

    this.closeWarning.set(false);
    this.queue.start(item => ({
      albumId: targetAlbumId,
      payload: {
        file: item.file,
        title: item.title.trim() || null,
        caption: this.applyCaption ? this.sharedCaption.trim() || null : null,
        taken_at: this.applyTakenAt ? this.sharedTakenAt || null : null,
        is_favorite: this.allowFavorite() && this.applyFavorite ? this.sharedFavorite : false,
        resolution: item.resolution ?? null,
        duration: item.duration ?? null,
        latitude: this.applyLocation ? this.manualLatitude : null,
        longitude: this.applyLocation ? this.manualLongitude : null,
      },
    }));
  }

  retry(item: QueueItem) {
    this.error.set('');
    this.queue.retry(item.id);
  }

  /** `albumId` es null cuando el existente no está en ningún álbum (L10B);
   * `inTarget` dice si ya está en el álbum al que se subía. */
  duplicateInfo(item: QueueItem): { mediaId: number; albumId: number | null; inTarget: boolean } | null {
    if (item.errorCode !== 'exact_duplicate') return null;
    const mediaId = Number(item.errorDetails?.['existing_media_id']);
    if (!Number.isSafeInteger(mediaId) || mediaId <= 0) return null;
    const raw = Number(item.errorDetails?.['existing_album_id']);
    const albumId = Number.isSafeInteger(raw) && raw > 0 ? raw : null;
    return { mediaId, albumId, inTarget: albumId !== null && albumId === item.payload?.albumId };
  }

  /** Mete en este álbum el recuerdo que ya existía: sin subir bytes otra vez. */
  addExisting(item: QueueItem) {
    const duplicate = this.duplicateInfo(item);
    const targetAlbumId = item.payload?.albumId;
    if (!duplicate || duplicate.inTarget || !targetAlbumId || !this.canAddExisting()) return;
    this.error.set('');
    this.api.addToAlbum(targetAlbumId, duplicate.mediaId).subscribe({
      next: () => {
        if (this.queue.resolve(item.id)) this.reused.update(current => new Set(current).add(item.id));
      },
      error: error => this.error.set(error?.error?.message || 'No se pudo añadir el recuerdo existente.'),
    });
  }

  forceDuplicate(item: QueueItem) {
    if (!this.duplicateInfo(item) || !item.payload) return;
    this.error.set('');
    this.queue.retry(item.id, {
      ...item.payload,
      payload: { ...item.payload.payload, force_duplicate: true },
    });
  }

  cancel(item: QueueItem) {
    if (this.queue.cancel(item.id) && item.state === 'queued') this.stopMetadataRead(item.id);
  }

  requestClose() {
    if (this.queue.hasActive() || this.queue.hasQueued()) {
      this.closeWarning.set(true);
      return;
    }
    this.closed.emit();
  }

  cancelAndClose() {
    const queuedIds = this.queue.items().filter(item => item.state === 'queued').map(item => item.id);
    const processing = this.queue.cancelAllCancelable();
    queuedIds.forEach(id => this.stopMetadataRead(id));
    if (processing) {
      this.error.set('Hay archivos que el servidor ya está procesando. Espera a que terminen antes de cerrar.');
      this.closeWarning.set(false);
      return;
    }
    this.closed.emit();
  }

  stateLabel(state: UploadQueueState) {
    return ({
      queued: 'En espera',
      uploading: 'Subiendo',
      processing: 'Procesando',
      success: 'Completado',
      failed: 'Falló',
      cancelled: 'Cancelado',
    } satisfies Record<UploadQueueState, string>)[state];
  }

  fileSizeLabel(bytes: number) {
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  private uploadItem(item: QueueItem): Observable<UploadQueueEvent<Media>> {
    return new Observable(subscriber => {
      if (!item.payload) {
        subscriber.error(new Error('No se pudo preparar el archivo.'));
        return;
      }
      const subscription = this.api.createMediaWithProgress(item.payload.albumId, item.payload.payload).subscribe({
        next: event => {
          if (event.type === HttpEventType.UploadProgress) {
            if (event.total && event.loaded >= event.total) subscriber.next({ type: 'processing' });
            else subscriber.next({ type: 'progress', loaded: event.loaded, total: event.total });
          } else if (event.type === HttpEventType.Response) {
            subscriber.next({ type: 'success', result: event.body!.data });
            this.uploaded.emit(event.body!.data);
            subscriber.complete();
          }
        },
        error: error => subscriber.error(error),
        complete: () => subscriber.complete(),
      });
      return () => subscription.unsubscribe();
    });
  }

  private isAccepted(file: File) {
    if (file.type.startsWith('image/') || file.type.startsWith('video/')) return true;
    const extension = file.name.split('.').pop()?.toLowerCase() || '';
    return !file.type && ACCEPTED_EXTENSIONS.has(extension);
  }

  private stopMetadataRead(id: number) {
    this.metadataReaders.get(id)?.();
    this.metadataPending.update(current => {
      const next = new Set(current);
      next.delete(id);
      return next;
    });
  }

  private readMediaMetadata(file: File, id: number): Promise<{ resolution: string | null; duration: number | null }> {
    return new Promise(resolve => {
      const url = URL.createObjectURL(file);
      let settled = false;
      let timeoutId = 0;
      const finish = (metadata: { resolution: string | null; duration: number | null }) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timeoutId);
        this.metadataReaders.delete(id);
        URL.revokeObjectURL(url);
        resolve(metadata);
      };
      this.metadataReaders.set(id, () => finish({ resolution: null, duration: null }));
      timeoutId = window.setTimeout(
        () => finish({ resolution: null, duration: null }),
        10_000,
      );
      if (file.type.startsWith('image/')) {
        const image = new Image();
        image.onload = () => finish({ resolution: `${image.naturalWidth}x${image.naturalHeight}`, duration: null });
        image.onerror = () => finish({ resolution: null, duration: null });
        image.src = url;
        return;
      }
      const video = document.createElement('video');
      video.preload = 'metadata';
      video.onloadedmetadata = () => finish({
        resolution: video.videoWidth && video.videoHeight ? `${video.videoWidth}x${video.videoHeight}` : null,
        duration: Number.isFinite(video.duration) ? Math.round(video.duration) : null,
      });
      video.onerror = () => finish({ resolution: null, duration: null });
      video.src = url;
    });
  }
}
