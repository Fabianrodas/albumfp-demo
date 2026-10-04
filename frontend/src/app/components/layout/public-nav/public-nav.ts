import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';
import { Icon } from '../../ui/icon/icon';
import { MenuItem, UserMenu } from '../../ui/user-menu/user-menu';
import { Theme } from '../../../core/services/theme';
import { Auth } from '../../../core/services/auth';
import { sessionHint } from '../../../core/services/session-hint';

/**
 * Navegación pública, la misma en las cinco páginas: no cambia de personalidad
 * según dónde esté. Se queda pegada arriba sobre papel translúcido.
 */
@Component({
  selector: 'app-public-nav',
  imports: [RouterLink, RouterLinkActive, Icon, UserMenu],
  templateUrl: './public-nav.html',
  styleUrl: './public-nav.css',
  host: { '(document:keydown.escape)': 'close()' },
})
export class PublicNav implements OnInit {
  readonly theme = inject(Theme);
  readonly auth = inject(Auth);
  readonly open = signal(false);

  /** La portada no tiene guard, así que nadie ha resuelto la sesión al llegar.
   * Va en `ngOnInit` y no en el constructor: `Auth` documenta por qué pedirse a
   * sí mismo durante su construcción rompía la sesión al recargar.
   *
   * El `maybeSignedIn()` no es una optimización: sin él, un visitante SIN
   * cuenta provocaría un `GET /auth/me` en la portada. La cookie de sesión es
   * HttpOnly y no se puede mirar, pero la CSRF se pone y se quita con ella y
   * sí es legible, así que sirve de pista. Si mintiera, el 401 lo resuelve. */
  ngOnInit() {
    if (sessionHint.maybeSignedIn() && !this.auth.user()) this.auth.ensureSession().subscribe();
  }

  readonly menu: MenuItem[] = [
    { label: 'Ir al panel', icon: 'albums', path: '/inicio' },
    { label: 'Cerrar sesión', icon: 'logout', danger: true, separatorBefore: true, action: () => this.auth.logout().subscribe() },
  ];

  toggle() { this.open.update(v => !v); }
  close() { this.open.set(false); }
}
