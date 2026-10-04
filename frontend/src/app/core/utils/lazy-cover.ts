import { DestroyRef, ElementRef, Signal, afterNextRender, effect, inject, untracked } from '@angular/core';
import { Subscription } from 'rxjs';
import { AlbumApi } from '../services/album-api';
import { blobUrlSignal } from './blob-url-signal';
import { onceVisible } from './visibility';

/**
 * URL de la portada de un álbum, descargada recién cuando la tarjeta entra en
 * pantalla. Una cuadrícula de álbumes pediría todas las portadas a la vez sin
 * esto.
 *
 * Pide la **vista previa** (fase 13), no el original: una portada se pinta a
 * unos cientos de píxeles y descargar la foto entera para eso era justo el
 * gasto que esa fase venía a quitar. El servidor cae al original solo cuando
 * esa foto no tiene vista previa, así que no hay nada que comprobar aquí.
 *
 * Se llama desde el contexto de inyección de un componente (un inicializador
 * de campo o el constructor); toma su elemento anfitrión para observarlo y
 * limpia la suscripción y la object URL al destruirse.
 */
export function lazyCoverUrl(
  coverId: () => number | null | undefined,
  loadable: () => boolean = () => true,
): Signal<string> {
  const api = inject(AlbumApi);
  const host = inject(ElementRef<HTMLElement>);
  const destroyRef = inject(DestroyRef);

  const cover = blobUrlSignal();
  let visible = false;
  let request: Subscription | undefined;
  let observer: IntersectionObserver | undefined;

  const load = () => {
    const id = coverId();
    if (!id || !loadable() || request || cover.value()) return;
    request = api.mediaPreview(id).subscribe({
      next: blob => cover.set(blob),
      error: () => { request = undefined; },
    });
  };

  // Cambiar de portada descarta la anterior y, si la tarjeta ya está visible,
  // pide la nueva de inmediato.
  effect(() => {
    coverId();
    loadable();
    untracked(() => {
      request?.unsubscribe();
      request = undefined;
      cover.clear();
      if (visible) load();
    });
  });

  afterNextRender(() => {
    observer = onceVisible(host, () => { visible = true; load(); });
  });

  destroyRef.onDestroy(() => {
    observer?.disconnect();
    request?.unsubscribe();
    cover.clear();
  });

  return cover.value;
}
