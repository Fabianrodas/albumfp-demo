import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AlbumApi } from '../../../core/services/album-api';
import { AssetComment, CommentsApi, MAX_COMMENT_LENGTH } from '../../../core/services/comments-api';
import { Confirm } from '../../../core/services/confirm';
import { Toast } from '../../../core/services/toast';

/**
 * Comentarios de un recuerdo (F05), debajo del visor del detalle.
 *
 * El servidor decide quién lee y quién comenta: esta vista no deduce nada de
 * las capacidades del álbum (comentar no es escribir en el álbum). Con
 * `shareToken` (enlace público, anónimo) es solo lectura: ni compositor ni
 * editar/borrar. El cuerpo se pinta con interpolación normal —texto escapado,
 * nunca como HTML— y `white-space: pre-wrap` conserva los saltos de línea.
 */
@Component({
  selector: 'app-media-comments',
  imports: [DatePipe, FormsModule],
  templateUrl: './media-comments.html',
  styleUrl: './media-comments.css',
})
export class MediaComments {
  private readonly api = inject(CommentsApi);
  private readonly albumApi = inject(AlbumApi);
  private readonly confirm = inject(Confirm);
  private readonly toast = inject(Toast);

  mediaId = input.required<number>();
  shareToken = input<string>('');

  readonly max = MAX_COMMENT_LENGTH;
  readonly readOnly = computed(() => !!this.shareToken());
  readonly comments = signal<AssetComment[]>([]);
  readonly total = signal(0);
  readonly page = signal(0);
  readonly totalPages = signal(0);
  readonly loading = signal(false);
  readonly error = signal('');
  readonly posting = signal(false);
  readonly editingId = signal<number | null>(null);
  readonly savingEdit = signal(false);
  readonly composeError = signal('');
  draft = '';
  editDraft = '';
  private epoch = 0;

  readonly hasMore = computed(() => this.page() < this.totalPages());

  constructor() {
    // Un recuerdo nuevo (flechas del detalle) empieza su propia lista.
    effect(() => {
      this.mediaId();
      untracked(() => this.reset());
    });
  }

  avatarUrl(userId: number) { return this.albumApi.avatarUrl(userId); }
  initials(name: string) { return (name || '?').slice(0, 2).toUpperCase(); }
  trimmedLength(value: string) { return value.trim().length; }

  reset() {
    this.epoch += 1;
    this.comments.set([]);
    this.total.set(0);
    this.page.set(0);
    this.totalPages.set(0);
    this.editingId.set(null);
    this.draft = '';
    this.composeError.set('');
    this.loadNext();
  }

  loadNext() {
    const epoch = this.epoch;
    const next = this.page() + 1;
    this.loading.set(true);
    this.error.set('');
    this.api.list(this.mediaId(), next, this.shareToken() || undefined).subscribe({
      next: response => {
        if (epoch !== this.epoch) return;
        const pagination = response.meta?.pagination;
        const seen = new Set(this.comments().map(c => c.id));
        this.comments.update(list => [...list, ...response.data.filter(c => !seen.has(c.id))]);
        this.page.set(next);
        this.totalPages.set(pagination?.total_pages ?? next);
        this.total.set(pagination?.total ?? this.comments().length);
        this.loading.set(false);
      },
      error: error => {
        if (epoch !== this.epoch) return;
        this.loading.set(false);
        this.error.set(error?.error?.message || 'No se pudieron cargar los comentarios.');
      },
    });
  }

  submit() {
    const body = this.draft.trim();
    if (!body || this.posting() || this.readOnly()) return;
    if (body.length > this.max) { this.composeError.set(`Máximo ${this.max} caracteres.`); return; }
    this.posting.set(true);
    this.composeError.set('');
    this.api.create(this.mediaId(), body).subscribe({
      next: response => {
        this.posting.set(false);
        this.draft = '';
        // El nuevo va al final del orden cronológico; si aún quedan páginas
        // por cargar, llegará con ellas y no se duplica (se filtra por id).
        if (!this.hasMore()) this.comments.update(list => [...list, response.data]);
        this.total.update(n => n + 1);
      },
      error: error => {
        this.posting.set(false);
        this.composeError.set(error?.error?.message || 'No se pudo publicar el comentario.');
      },
    });
  }

  startEdit(comment: AssetComment) {
    this.editingId.set(comment.id);
    this.editDraft = comment.body;
  }

  cancelEdit() { this.editingId.set(null); }

  saveEdit(comment: AssetComment) {
    const body = this.editDraft.trim();
    if (!body || this.savingEdit()) return;
    this.savingEdit.set(true);
    this.api.update(comment.id, body).subscribe({
      next: response => {
        this.savingEdit.set(false);
        this.editingId.set(null);
        this.comments.update(list => list.map(c => (c.id === comment.id ? response.data : c)));
      },
      error: error => {
        this.savingEdit.set(false);
        this.toast.error(error?.error?.message || 'No se pudo editar el comentario.');
      },
    });
  }

  async remove(comment: AssetComment) {
    const confirmed = await this.confirm.ask({
      title: 'Eliminar comentario',
      message: 'El comentario desaparecerá para todas las personas que ven este recuerdo.',
      confirmLabel: 'Eliminar',
      danger: true,
    });
    if (!confirmed) return;
    this.api.remove(comment.id).subscribe({
      next: () => {
        this.comments.update(list => list.filter(c => c.id !== comment.id));
        this.total.update(n => Math.max(0, n - 1));
      },
      error: () => this.toast.error('No se pudo eliminar el comentario.'),
    });
  }
}
