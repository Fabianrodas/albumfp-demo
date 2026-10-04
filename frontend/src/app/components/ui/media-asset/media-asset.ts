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
  /** Solo importa con shareToken: si el dueño del enlace permite bajar el
   * original. Ver el comentario en load() -- un video sin este permiso no
   * tiene ningún derivado seguro que pedir. */
  allowOriginalDownload = input(true);
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
    // P05 no genera posters: hacerlo de forma mantenible exigiria FFmpeg o
    // libav. En una cuadricula, pedir /preview caeria al original y podria
    // bajar cientos de MB solo para pintar una tarjeta; se muestra un estado
    // estatico y el original queda reservado al detalle explicito.
    if (isVideo && this.quality() === 'preview') {
      this.videoPlaceholder.set(true);
      return;
    }
    // En calidad original el detalle conserva la reproduccion de video.
    // En un enlace público (S08) el original con `allow_original_download`
    // apagado da 403; sin ningun derivado seguro, no se intenta la peticion.
    if (token && isVideo && !this.allowOriginalDownload()) {
      this.failed.set(true);
      return;
    }
    const preview = this.quality() === 'preview';
    const request = token
      ? (preview ? this.api.sharedMediaPreview(token, item.id) : this.api.sharedMediaFile(token, item.id))
      : (preview ? this.api.mediaPreview(item.id) : this.api.mediaFile(item.id));
    this.request = request.subscribe({
      next: blob => this.asset.set(blob),
      error: () => this.failed.set(true),
    });
  }
}
