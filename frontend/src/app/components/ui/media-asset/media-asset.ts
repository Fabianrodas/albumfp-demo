import { Component, ElementRef, OnDestroy, AfterViewInit, inject, input, signal } from '@angular/core';
import { Subscription } from 'rxjs';
import { AlbumApi, Media } from '../../../core/services/album-api';
import { blobUrlSignal } from '../../../core/utils/blob-url-signal';
import { onceVisible } from '../../../core/utils/visibility';
import { Icon } from '../icon/icon';

@Component({
  selector: 'app-media-asset',
  imports: [Icon],
  templateUrl: './media-asset.html',
  styleUrl: './media-asset.css',
})
export class MediaAsset implements AfterViewInit, OnDestroy {
  private readonly api = inject(AlbumApi);
  private readonly host = inject(ElementRef<HTMLElement>);
  private observer?: IntersectionObserver;
  private request?: Subscription;

  item = input.required<Media>();
  shareToken = input<string | null>(null);
  /**
   * `preview` pide la versión reducida; `original` el archivo entero. Por
   * defecto reducida, que es para lo que sirve este componente (pintar una
   * tarjeta). El visor de una foto no lo usa: pide el original por su cuenta.
   */
  quality = input<'preview' | 'original'>('preview');
  private readonly asset = blobUrlSignal();
  url = this.asset.value;
  failed = signal(false);
  videoPlaceholder = signal(false);

  ngAfterViewInit() {
    this.observer = onceVisible(this.host, () => this.load());
  }

  ngOnDestroy() {
    this.observer?.disconnect();
    this.request?.unsubscribe();
    this.asset.clear();
  }

  private load() {
    if (this.request || this.url()) return;
    const item = this.item();
    const token = this.shareToken();
    const isVideo = item.file_type === 'video';
    // v1.1: la vista previa de un video es su PORTADA (el fotograma que se
    // eligió al subirlo). Sin portada el servidor responde 404 -- nunca el
    // original entero -- y la tarjeta pinta su marcador de video.
    const preview = this.quality() === 'preview';
    const request = token
      ? (preview ? this.api.sharedMediaPreview(token, item.id) : this.api.sharedMediaFile(token, item.id))
      : (preview ? this.api.mediaPreview(item.id) : this.api.mediaFile(item.id));
    this.request = request.subscribe({
      next: blob => this.asset.set(blob),
      error: () => (isVideo && preview ? this.videoPlaceholder : this.failed).set(true),
    });
  }
}
