import { ElementRef } from '@angular/core';

/** Runs `callback` once `host` scrolls into view, then disconnects. Runs immediately if IntersectionObserver is unavailable. */
export function onceVisible(host: ElementRef<HTMLElement>, callback: () => void, rootMargin = '320px 0px'): IntersectionObserver | undefined {
  if (typeof IntersectionObserver === 'undefined') {
    callback();
    return undefined;
  }
  const observer = new IntersectionObserver(entries => {
    if (!entries.some(entry => entry.isIntersecting)) return;
    observer.disconnect();
    callback();
  }, { rootMargin });
  observer.observe(host.nativeElement);
  return observer;
}
