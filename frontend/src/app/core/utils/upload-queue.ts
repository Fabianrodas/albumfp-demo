import { computed, signal } from '@angular/core';
import { Observable, Subscription } from 'rxjs';

export type UploadQueueState =
  'queued' | 'uploading' | 'processing' | 'success' | 'failed' | 'cancelled';

export type UploadQueueEvent<TResult> =
  | { type: 'progress'; loaded: number; total?: number }
  | { type: 'processing' }
  | { type: 'success'; result: TResult };

export interface UploadQueueDraft {
  file: File;
  title: string;
  resolution?: string | null;
  duration?: number | null;
}

export interface UploadQueueItem<TPayload, TResult> extends UploadQueueDraft {
  id: number;
  state: UploadQueueState;
  progress: number;
  error: string;
  errorCode?: string;
  errorDetails?: Readonly<Record<string, string | number | boolean | null>>;
  retryable: boolean;
  payload?: TPayload;
  result?: TResult;
}

/**
 * Orquestador local: limita requests, pero nunca agrupa archivos. El adapter
 * sigue invocando una vez por elemento el endpoint seguro de upload.
 */
export class UploadQueue<TPayload, TResult> {
  readonly items = signal<UploadQueueItem<TPayload, TResult>[]>([]);
  readonly activeCount = signal(0);
  readonly running = signal(false);
  readonly hasActive = computed(() => this.items().some(item =>
    item.state === 'uploading' || item.state === 'processing'));
  readonly hasProcessing = computed(() => this.items().some(item => item.state === 'processing'));
  readonly hasQueued = computed(() => this.items().some(item => item.state === 'queued'));
  readonly successCount = computed(() => this.items().filter(item => item.state === 'success').length);

  private nextId = 1;
  private readonly subscriptions = new Map<number, Subscription>();

  constructor(
    private readonly upload: (item: UploadQueueItem<TPayload, TResult>) => Observable<UploadQueueEvent<TResult>>,
    private readonly concurrency = 3,
  ) {
    if (!Number.isInteger(concurrency) || concurrency < 1) {
      throw new Error('La concurrencia debe ser un entero positivo');
    }
  }

  add(drafts: UploadQueueDraft[]): number[] {
    const ids: number[] = [];
    const additions = drafts.map(draft => {
      const id = this.nextId++;
      ids.push(id);
      return {
        ...draft,
        id,
        state: 'queued' as const,
        progress: 0,
        error: '',
        retryable: false,
      };
    });
    this.items.update(current => [...current, ...additions]);
    return ids;
  }

  item(id: number) {
    return this.items().find(item => item.id === id);
  }

  updateTitle(id: number, title: string) {
    const current = this.item(id);
    if (!current || current.state !== 'queued') return;
    this.patch(id, { title: title.slice(0, 120) });
  }

  updateMetadata(id: number, metadata: Pick<UploadQueueDraft, 'resolution' | 'duration'>) {
    const current = this.item(id);
    if (!current || current.state !== 'queued') return;
    this.patch(id, metadata);
  }

  start(payloadFactory: (item: UploadQueueItem<TPayload, TResult>) => TPayload) {
    this.items.update(current => current.map(item => {
      if (item.state !== 'queued' || item.payload !== undefined) return item;
      return {
        ...item,
        payload: payloadFactory(item),
        error: '',
        errorCode: undefined,
        errorDetails: undefined,
        progress: 0,
      };
    }));
    this.running.set(true);
    this.pump();
  }

  retry(id: number, replacementPayload?: TPayload): boolean {
    const current = this.item(id);
    if (!current || current.state !== 'failed' || !current.retryable || current.payload === undefined) return false;
    this.patch(id, {
      state: 'queued',
      payload: replacementPayload ?? current.payload,
      error: '',
      errorCode: undefined,
      errorDetails: undefined,
      progress: 0,
      retryable: false,
      result: undefined,
    });
    this.running.set(true);
    this.pump();
    return true;
  }

  /**
   * Da por resuelto un fallo sin subir nada: p. ej. un duplicado exacto que se
   * añadió al álbum reutilizando el recuerdo que ya existía (L10B).
   */
  resolve(id: number): boolean {
    const current = this.item(id);
    if (!current || current.state !== 'failed') return false;
    this.patch(id, { state: 'success', error: '', errorCode: undefined, errorDetails: undefined, retryable: false });
    return true;
  }

  cancel(id: number): boolean {
    const current = this.item(id);
    if (!current || !['queued', 'uploading'].includes(current.state)) return false;
    this.patch(id, { state: 'cancelled', error: '', progress: 0 });
    if (current.state === 'uploading') {
      const subscription = this.subscriptions.get(id);
      this.subscriptions.delete(id);
      this.activeCount.update(value => Math.max(0, value - 1));
      subscription?.unsubscribe();
      this.pump();
    } else {
      this.finishIfSettled();
    }
    return true;
  }

  /** Cancela lo que todavía es abortable. Devuelve cuántos ya procesan. */
  cancelAllCancelable(): number {
    this.running.set(false);
    for (const item of this.items()) {
      if (item.state === 'queued') {
        this.patch(item.id, { state: 'cancelled', error: '', progress: 0 });
      } else if (item.state === 'uploading') {
        this.patch(item.id, { state: 'cancelled', error: '', progress: 0 });
        const subscription = this.subscriptions.get(item.id);
        this.subscriptions.delete(item.id);
        this.activeCount.update(value => Math.max(0, value - 1));
        subscription?.unsubscribe();
      }
    }
    return this.items().filter(item => item.state === 'processing').length;
  }

  private pump() {
    if (!this.running()) return;
    while (this.activeCount() < this.concurrency) {
      const next = this.items().find(item => item.state === 'queued' && item.payload !== undefined);
      if (!next) break;
      this.startOne(next);
    }
    this.finishIfSettled();
  }

  private startOne(item: UploadQueueItem<TPayload, TResult>) {
    this.patch(item.id, {
      state: 'uploading',
      progress: 0,
      error: '',
      errorCode: undefined,
      errorDetails: undefined,
    });
    this.activeCount.update(value => value + 1);
    const container = new Subscription();
    this.subscriptions.set(item.id, container);
    container.add(this.upload({ ...item, state: 'uploading' }).subscribe({
      next: event => {
        if (event.type === 'progress') {
          const total = event.total || 0;
          const progress = total > 0 ? Math.min(99, Math.round(event.loaded * 100 / total)) : 0;
          this.patch(item.id, { state: 'uploading', progress });
        } else if (event.type === 'processing') {
          this.patch(item.id, { state: 'processing', progress: 100 });
        } else {
          this.patch(item.id, { state: 'success', progress: 100, result: event.result, error: '' });
          this.finishRequest(item.id);
        }
      },
      error: error => {
        // Solo 4xx confirma que el servidor respondió rechazando esta
        // operación. Un corte de transporte o 5xx puede ocultar un COMMIT ya
        // confirmado: repetirlo crearía otro media.
        const status = Number(error?.status);
        const retryable = Number.isFinite(status) && status >= 400 && status < 500;
        const body = error?.error;
        const rawCode = body && typeof body === 'object' && !Array.isArray(body) ? body.code : undefined;
        const errorCode = typeof rawCode === 'string' && /^[a-z0-9_]{1,80}$/.test(rawCode)
          ? rawCode
          : undefined;
        const errorDetails: Record<string, number> = {};
        for (const key of ['existing_media_id', 'existing_album_id'] as const) {
          const value = body && typeof body === 'object' && !Array.isArray(body) ? body[key] : undefined;
          if (Number.isSafeInteger(value) && value > 0) errorDetails[key] = value;
        }
        this.patch(item.id, {
          state: 'failed',
          retryable,
          errorCode,
          errorDetails: Object.keys(errorDetails).length ? errorDetails : undefined,
          error: retryable
            ? error?.error?.message || error?.message || 'No se pudo subir el archivo.'
            : 'No se confirmó el resultado. Revisa el álbum antes de volver a subir este archivo.',
        });
        this.finishRequest(item.id);
      },
      complete: () => {
        const current = this.item(item.id);
        if (current?.state === 'uploading' || current?.state === 'processing') {
          this.patch(item.id, {
            state: 'failed',
            retryable: false,
            error: 'No se confirmó el resultado. Revisa el álbum antes de volver a subir este archivo.',
          });
          this.finishRequest(item.id);
        }
      },
    }));
  }

  private finishRequest(id: number) {
    const subscription = this.subscriptions.get(id);
    if (!subscription) return;
    this.subscriptions.delete(id);
    this.activeCount.update(value => Math.max(0, value - 1));
    subscription.unsubscribe();
    this.pump();
  }

  private finishIfSettled() {
    if (!this.items().some(item => item.state === 'queued' && item.payload !== undefined) && this.activeCount() === 0) {
      this.running.set(false);
    }
  }

  private patch(id: number, changes: Partial<UploadQueueItem<TPayload, TResult>>) {
    this.items.update(current => current.map(item => item.id === id ? { ...item, ...changes } : item));
  }
}
