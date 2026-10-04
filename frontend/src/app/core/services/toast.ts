import { Injectable, signal } from '@angular/core';

export interface ToastItem {
  id: number;
  kind: 'success' | 'error';
  message: string;
}

const DISMISS_AFTER_MS = 4000;

/** Avisos breves en la esquina superior derecha. Los dibuja `app-toast-host`. */
@Injectable({ providedIn: 'root' })
export class Toast {
  readonly items = signal<ToastItem[]>([]);
  private lastId = 0;

  success(message: string) { this.push('success', message); }
  error(message: string) { this.push('error', message); }

  dismiss(id: number) {
    this.items.update(list => list.filter(item => item.id !== id));
  }

  private push(kind: ToastItem['kind'], message: string) {
    const id = ++this.lastId;
    this.items.update(list => [...list, { id, kind, message }]);
    setTimeout(() => this.dismiss(id), DISMISS_AFTER_MS);
  }
}
