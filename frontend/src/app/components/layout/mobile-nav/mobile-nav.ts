import { Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink, RouterLinkActive } from '@angular/router';
import { filter, map } from 'rxjs';
import { DialogTrap } from '../../../core/directives/dialog-trap';
import { Theme } from '../../../core/services/theme';
import { Icon, IconName } from '../../ui/icon/icon';

interface Destino { path: string; label: string; icon: IconName }

/** Las cuatro de uso diario van en la barra; el resto, en «Más». */
export const TABS: Destino[] = [
  { path: '/inicio', label: 'Inicio', icon: 'home' },
  { path: '/biblioteca', label: 'Biblioteca', icon: 'calendar' },
  { path: '/albumes', label: 'Álbumes', icon: 'albums' },
  { path: '/compartido', label: 'Compartido', icon: 'share' },
];
export const MORE: Destino[] = [
  { path: '/lugares', label: 'Lugares', icon: 'pin' },
  { path: '/favoritos', label: 'Favoritos', icon: 'heart' },
  { path: '/archivo', label: 'Archivo', icon: 'archive' },
  { path: '/papelera', label: 'Papelera', icon: 'trash' },
];

/**
 * Navegación del panel en el teléfono: barra inferior al alcance del pulgar.
 *
 * Por debajo de 760px sustituye al carril lateral, que se comía 62px de 390 y
 * dejaba ocho iconos sin nombre. Con la barra, el contenido recupera el ancho
 * entero y cada destino lleva su nombre. Lo que antes solo vivía en el carril
 * (el tema) está en «Más»; cerrar sesión sigue en el menú de la cuenta (F03).
 * Por encima de 760px no se pinta (`display:none`): manda el sidebar.
 */
@Component({
  selector: 'app-mobile-nav',
  imports: [RouterLink, RouterLinkActive, Icon, DialogTrap],
  templateUrl: './mobile-nav.html',
  styleUrl: './mobile-nav.css',
})
export class MobileNav {
  readonly theme = inject(Theme);
  private readonly router = inject(Router);
  readonly tabs = TABS;
  readonly more = MORE;
  readonly open = signal(false);

  private readonly url = toSignal(
    this.router.events.pipe(filter(e => e instanceof NavigationEnd), map(() => this.router.url)),
    { initialValue: this.router.url });
  /** «Más» se marca cuando la página actual es una de las suyas. */
  readonly moreActive = computed(() => MORE.some(d => this.url().startsWith(d.path)));

  readonly close = () => this.open.set(false);
  toggle() { this.open.update(v => !v); }
}
