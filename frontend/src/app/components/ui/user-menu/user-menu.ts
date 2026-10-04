import { Component, ElementRef, computed, inject, input, signal } from '@angular/core';
import { Router } from '@angular/router';
import { Auth } from '../../../core/services/auth';
import { Icon, IconName } from '../icon/icon';

export interface MenuItem {
  label: string;
  icon: IconName;
  /** Uno de los dos: navegar, o ejecutar. */
  path?: string;
  action?: () => void;
  danger?: boolean;
  /** Una línea antes de la opción: separa lo destructivo del resto. */
  separatorBefore?: boolean;
}

/**
 * El avatar de la cuenta con su menú. Lo usan la navegación pública y la del
 * panel: es el mismo control, y lo único que cambia son las opciones, así que
 * llegan por input en vez de duplicar el componente.
 *
 * Teclado (patrón «menu button» de WAI-ARIA): abrir enfoca la primera opción;
 * flechas, Inicio y Fin se mueven entre opciones; Escape cierra y devuelve el
 * foco al avatar; Tab cierra y deja seguir. Un clic fuera también cierra.
 */
@Component({
  selector: 'app-user-menu',
  imports: [Icon],
  templateUrl: './user-menu.html',
  styleUrl: './user-menu.css',
  host: {
    '(document:keydown.escape)': 'escape()',
    '(document:click)': 'onDocumentClick($event)',
  },
})
export class UserMenu {
  readonly auth = inject(Auth);
  private readonly host = inject(ElementRef<HTMLElement>);
  private readonly router = inject(Router);

  items = input.required<MenuItem[]>();
  /** Enseña el nombre junto al avatar. En el panel no cabe. */
  showName = input(false);

  readonly open = signal(false);
  readonly initials = computed(() => (this.auth.user()?.username || 'AlbumFP').slice(0, 2).toUpperCase());

  toggle() {
    this.open.update(v => !v);
    if (this.open()) setTimeout(() => this.focusItem(0));
  }

  close() { this.open.set(false); }

  escape() {
    if (!this.open()) return;
    this.close();
    this.trigger()?.focus();
  }

  /** Un menú se cierra al tocar fuera. No es un modal: no atrapa el foco. */
  onDocumentClick(event: MouseEvent) {
    if (this.open() && !this.host.nativeElement.contains(event.target as Node)) this.close();
  }

  onMenuKeydown(event: KeyboardEvent) {
    const options = this.options();
    const current = options.indexOf(document.activeElement as HTMLElement);
    const moves: Record<string, number> = {
      ArrowDown: (current + 1) % options.length,
      ArrowUp: (current - 1 + options.length) % options.length,
      Home: 0,
      End: options.length - 1,
    };
    if (event.key in moves) {
      event.preventDefault();
      this.focusItem(moves[event.key]);
    } else if (event.key === 'Tab') {
      this.close();
    }
  }

  run(item: MenuItem) {
    this.close();
    if (item.action) item.action();
    else if (item.path) this.router.navigateByUrl(item.path);
  }

  private options(): HTMLElement[] {
    return [...this.host.nativeElement.querySelectorAll('[role="menuitem"]')] as HTMLElement[];
  }

  private focusItem(index: number) { this.options()[index]?.focus(); }

  private trigger(): HTMLElement | null {
    return this.host.nativeElement.querySelector('.user-menu__trigger');
  }
}
