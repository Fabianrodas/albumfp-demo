import { Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';
import { Theme } from '../../../core/services/theme';
import { Icon } from '../../ui/icon/icon';

@Component({ selector: 'app-sidebar', imports: [RouterLink, RouterLinkActive, Icon], templateUrl: './sidebar.html', styleUrl: './sidebar.css' })
export class Sidebar {
  /** Cerrar sesión ya no vive aquí (F03): está en el menú de la cuenta. */
  readonly theme = inject(Theme);
}
