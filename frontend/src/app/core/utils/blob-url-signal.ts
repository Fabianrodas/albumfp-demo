import { signal } from '@angular/core';

/** A signal<string> holding an object URL, that revokes the previous URL whenever it's replaced or cleared. */
export function blobUrlSignal() {
  const value = signal('');
  return {
    value: value.asReadonly(),
    set(blob: Blob) {
      this.clear();
      value.set(URL.createObjectURL(blob));
    },
    clear() {
      const current = value();
      if (current) URL.revokeObjectURL(current);
      value.set('');
    },
  };
}
