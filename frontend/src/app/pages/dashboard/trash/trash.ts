import { Component, inject, signal } from '@angular/core';
import { MediaGrid } from '../../../components/ui/media-grid/media-grid';
import { AlbumApi, Media } from '../../../core/services/album-api';
import { Confirm } from '../../../core/services/confirm';
import { Toast } from '../../../core/services/toast';
import { ScrollReveal } from '../../../core/directives/scroll-reveal';

@Component({ selector: 'app-trash', imports: [MediaGrid, ScrollReveal], templateUrl: './trash.html', styleUrl: './trash.css' })
export class Trash {
  private readonly api = inject(AlbumApi);
  private readonly confirm = inject(Confirm);
  private readonly toast = inject(Toast);

  deleted = signal<Media[]>([]);
  busy = signal(false);
  loaded = signal(false);
  error = signal('');

  ngOnInit() { this.load(); }

  load() {
    this.api.trash().subscribe({
      next: r => { this.deleted.set(r.data); this.loaded.set(true); },
      error: e => {
        this.error.set(e?.error?.message || 'No se pudo cargar la papelera.');
        this.loaded.set(true);
      },
    });
  }

  restore(media: Media) {
    this.error.set('');
    this.api.restore(media.id).subscribe({
      next: () => {
        this.deleted.update(items => items.filter(item => item.id !== media.id));
        this.toast.success('Archivo restaurado.');
      },
      error: e => this.toast.error(e?.error?.message || 'No se pudo restaurar el archivo.'),
    });
  }

  async deletePermanent(media: Media) {
    const confirmed = await this.confirm.ask({
      title: 'Eliminar permanentemente',
      message: 'Este archivo se borrará del almacenamiento para siempre. No hay forma de recuperarlo.',
      confirmLabel: 'Eliminar para siempre',
      danger: true,
    });
    if (!confirmed) return;

    this.error.set('');
    this.api.deleteMediaPermanent(media.id).subscribe({
      next: () => {
        this.deleted.update(items => items.filter(item => item.id !== media.id));
        this.toast.success('Archivo eliminado permanentemente.');
      },
      error: e => this.toast.error(e?.error?.message || 'No se pudo eliminar el archivo permanentemente.'),
    });
  }

  restoreAll() {
    if (!this.deleted().length || this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    this.api.restoreTrashAll().subscribe({
      next: response => {
        this.deleted.set([]);
        this.busy.set(false);
        this.toast.success(`${response.data.restored_count} archivos restaurados.`);
      },
      error: e => {
        this.busy.set(false);
        this.toast.error(e?.error?.message || 'No se pudo restaurar la papelera.');
      },
    });
  }

  async deleteAll() {
    const total = this.deleted().length;
    if (!total || this.busy()) return;

    const confirmed = await this.confirm.ask({
      title: 'Vaciar la papelera',
      message: `Se borrarán del almacenamiento los ${total} archivos de la papelera. No hay forma de recuperarlos.`,
      confirmLabel: 'Borrar todo',
      danger: true,
    });
    if (!confirmed) return;

    this.busy.set(true);
    this.error.set('');
    this.api.deleteTrashAll().subscribe({
      next: () => {
        this.deleted.set([]);
        this.busy.set(false);
        this.toast.success('Papelera vaciada.');
      },
      error: e => {
        this.busy.set(false);
        this.toast.error(e?.error?.message || 'No se pudo vaciar la papelera.');
      },
    });
  }
}
