import { DatePipe } from '@angular/common';
import { Component, ElementRef, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { AppNotification, NotificationsApi } from '../../../core/services/notifications';
import { notificationLink, notificationText } from '../../../core/utils/activity-copy';
import { pagedList } from '../../../core/utils/paged-list';
import { Icon } from '../icon/icon';

/**
 * La campana de la topbar (L12): avisos in-app, nada de push ni de permisos del
 * navegador. Se refresca al arrancar el panel y cada vez que se abre -- sin
 * sondeo en segundo plano: un aviso de otra cuenta aparece al volver a abrirla.
 * Abrirla no marca nada como leído; tocar un aviso sí, y "Marcar todas" también.
 */
@Component({
  selector: 'app-notification-bell',
  imports: [DatePipe, Icon],
  templateUrl: './notification-bell.html',
  styleUrl: './notification-bell.css',
  host: {
    '(document:keydown.escape)': 'close()',
    '(document:click)': 'onDocumentClick($event)',
  },
})
export class NotificationBell {
  private readonly api = inject(NotificationsApi);
  private readonly router = inject(Router);
  private readonly host = inject(ElementRef<HTMLElement>);

  readonly open = signal(false);
  readonly unread = signal(0);
  readonly markingAll = signal(false);
  readonly list = pagedList<AppNotification>(page => this.api.list(page));

  readonly badge = computed(() => (this.unread() > 99 ? '99+' : String(this.unread())));
  readonly label = computed(() => (this.unread() ? `Notificaciones, ${this.unread()} sin leer` : 'Notificaciones'));
  readonly text = notificationText;

  constructor() { this.refreshCount(); }

  /** Un fallo del contador no rompe la topbar: la campana se queda sin globo. */
  refreshCount() {
    this.api.unreadCount().subscribe({ next: r => this.unread.set(r.data.unread), error: () => {} });
  }

  toggle() {
    if (this.open()) return this.close();
    this.open.set(true);
    this.list.reset();
    this.refreshCount();
  }

  close() { this.open.set(false); }

  onDocumentClick(event: MouseEvent) {
    if (this.open() && !this.host.nativeElement.contains(event.target as Node)) this.close();
  }

  select(notice: AppNotification) {
    const go = () => { this.close(); this.router.navigateByUrl(notificationLink(notice)); };
    if (notice.read_at) return go();
    this.api.markRead(notice.id).subscribe({
      next: r => {
        this.list.items.update(items => items.map(n => (n.id === notice.id ? { ...n, read_at: r.data.read_at } : n)));
        this.unread.update(n => Math.max(0, n - 1));
        go();
      },
      // Si marcar falla, se navega igual: leerlo es secundario a llegar.
      error: go,
    });
  }

  markAll() {
    this.markingAll.set(true);
    this.api.markAllRead().subscribe({
      next: () => {
        const now = new Date().toISOString();
        this.list.items.update(items => items.map(n => n.read_at ? n : { ...n, read_at: now }));
        this.unread.set(0);
        this.markingAll.set(false);
      },
      error: () => this.markingAll.set(false),
    });
  }
}
