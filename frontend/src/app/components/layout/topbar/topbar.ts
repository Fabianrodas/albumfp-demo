import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { Auth } from '../../../core/services/auth';
import { Theme } from '../../../core/services/theme';
import { Icon } from '../../ui/icon/icon';
import { NotificationBell } from '../../ui/notification-bell/notification-bell';
import { MenuItem, UserMenu } from '../../ui/user-menu/user-menu';

@Component({
  selector: 'app-topbar',
  imports: [FormsModule, Icon, NotificationBell, UserMenu],
  templateUrl: './topbar.html',
  styleUrl: './topbar.css',
})
export class Topbar {
  readonly auth = inject(Auth);
  readonly theme = inject(Theme);
  private readonly router = inject(Router);
  query = '';

  /** El menú de la cuenta (F03). "Ir al sitio público" y no "Inicio" porque
   * el carril lateral ya tiene un "Inicio" que significa otra cosa. Cerrar
   * sesión vive SOLO aquí (el carril ya no la repite), separada y en rojo. */
  readonly menu: MenuItem[] = [
    { label: 'Mi perfil', icon: 'user', path: '/perfil' },
    { label: 'Ir al sitio público', icon: 'home', path: '/' },
    { label: 'Cerrar sesión', icon: 'logout', danger: true, separatorBefore: true, action: () => this.logout() },
  ];

  /** Se navega dentro del `subscribe`: cerrar sesión revoca la fila en el
   * servidor, así que salir antes de que responda dejaría la sesión viva. */
  logout() {
    this.auth.logout().subscribe(() => this.router.navigateByUrl('/'));
  }

  search() {
    const q = this.query.trim();
    this.router.navigate(['/albumes'], { queryParams: q ? { q } : {} });
  }
}
