import { Injectable, computed, effect, signal } from '@angular/core';

export type ThemeMode = 'light' | 'dark';

@Injectable({ providedIn: 'root' })
export class Theme {
  private readonly storageKey = 'albumfp_theme';
  readonly mode = signal<ThemeMode>(this.readInitialMode());
  readonly isDark = computed(() => this.mode() === 'dark');

  /**
   * El logotipo que toca según el tema, en un solo sitio.
   *
   * El de trazo oscuro desaparece sobre el papel del modo oscuro, y ese fallo
   * estaba repetido en cinco plantillas: dos lo resolvían por su cuenta con un
   * `computed` idéntico y tres (404, reclamar enlace, enlace compartido) se
   * habían quedado con `logo.png` fijo.
   *
   * Ojo: NO vale para el sidebar ni para el pie público. Esos dos fondos son
   * verde oscuro en los dos temas, así que ahí siempre va el blanco literal.
   */
  readonly logo = computed(() => this.isDark() ? 'logo_white.png' : 'logo.png');

  constructor() {
    effect(() => {
      const mode = this.mode();
      document.documentElement.dataset['theme'] = mode;
      document.documentElement.style.colorScheme = mode;
      try { localStorage.setItem(this.storageKey, mode); } catch { /* storage can be unavailable */ }
    });
  }

  toggle() {
    this.mode.update(mode => mode === 'dark' ? 'light' : 'dark');
  }

  set(mode: ThemeMode) {
    this.mode.set(mode);
  }

  private readInitialMode(): ThemeMode {
    try {
      const stored = localStorage.getItem(this.storageKey);
      if (stored === 'dark' || stored === 'light') return stored;
    } catch { /* storage can be unavailable */ }
    // Sin preferencia guardada (primera visita, o storage bloqueado): se seguía
    // forzando claro sin mirar el sistema operativo, así que alguien con su
    // equipo en oscuro veía la app en claro hasta tocar el interruptor.
    if (typeof matchMedia !== 'undefined' && matchMedia('(prefers-color-scheme: dark)').matches) return 'dark';
    return 'light';
  }
}
