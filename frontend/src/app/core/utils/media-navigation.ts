export type MediaDirection = 'previous' | 'next';

export function neighboringMediaId(media: readonly { id: number }[], currentId: number, direction: MediaDirection) {
  const currentIndex = media.findIndex(item => item.id === currentId);
  if (currentIndex < 0) return null;
  return media[currentIndex + (direction === 'next' ? 1 : -1)]?.id ?? null;
}

export function navigationKey(key: string, target: EventTarget | null, dialogOpen = false): MediaDirection | null {
  if (dialogOpen) return null;
  const element = target as HTMLElement | null;
  const tagName = element?.tagName?.toLowerCase();
  if (element?.isContentEditable || ['input', 'textarea', 'select', 'video', 'audio'].includes(tagName || '')) return null;
  if (key === 'ArrowLeft') return 'previous';
  if (key === 'ArrowRight') return 'next';
  return null;
}

/** A media id may repeat after A → B → A, so id alone cannot identify the
 * currently active request. The incrementing load epoch makes it unique. */
export function isCurrentMediaRequest(expectedId: number, expectedEpoch: number, currentId: number, currentEpoch: number) {
  return expectedId === currentId && expectedEpoch === currentEpoch;
}

/** Ruta del detalle de un recuerdo (L10B). Con álbum, la vista en contexto
 * (`/albumes/:albumId/media/:mediaId`, que el servidor valida por
 * pertenencia); sin él —un recuerdo que no está en ningún álbum— la vista del
 * recuerdo en sí, `/recuerdos/:mediaId`. */
export function mediaDetailLink(albumId: number | null | undefined, mediaId: number): (string | number)[] {
  return albumId ? ['/albumes', albumId, 'media', mediaId] : ['/recuerdos', mediaId];
}
