import { Component, computed, inject, signal } from '@angular/core';
import { Router, RouterLink, RouterOutlet } from '@angular/router';
import { Theme } from '../../../core/services/theme';
import { ShotFrame } from '../../ui/shot-frame/shot-frame';
import { Icon } from '../../ui/icon/icon';
import { ScrollReveal } from '../../../core/directives/scroll-reveal';
import { PublicBackdrop } from '../../ui/public-backdrop/public-backdrop';

/** Cada pantalla de cuenta enseña su propio par de capturas y su propia frase. */
const PANELES = {
  login: {
    frase: 'Las cosas que guardas también cuentan tu historia.',
    principal: { src: 'capturas/paso-albumes.webp', zoom: 1 },
    apoyo: { src: 'capturas/paso-perfil-publico.webp', zoom: 1 },
  },
  registro: {
    frase: 'Empieza por una foto. El resto se ordena solo.',
    principal: { src: 'capturas/paso-inicio.webp', zoom: 1 },
    apoyo: { src: 'capturas/paso-favoritos.webp', zoom: 1 },
  },
} as const;

/**
 * Marco de las dos pantallas de cuenta: el formulario manda y el panel visual
 * acompaña. En vertical el panel desaparece y el formulario se queda la
 * pantalla entera.
 */
@Component({
  selector: 'app-auth-layout',
  imports: [PublicBackdrop, RouterLink, RouterOutlet, ShotFrame, Icon, ScrollReveal],
  templateUrl: './auth-layout.html',
  styleUrl: './auth-layout.css',
})
export class AuthLayout {
  /* La marca oscura desaparece sobre el papel oscuro. */
  readonly theme = inject(Theme);
  private readonly router = inject(Router);

  /* Se resuelve al activarse la ruta hija, así no hace falta tocar app.routes.ts
     ni importar los componentes de login y registro aquí. */
  private readonly ruta = signal(this.router.url);
  readonly panel = computed(() => this.ruta().startsWith('/registro') ? PANELES.registro : PANELES.login);

  alActivar() {
    this.ruta.set(this.router.url);
  }
}
