import { Injectable, signal } from '@angular/core';

/** El evento que Chrome/Edge disparan cuando la página se puede instalar. No
 * está en los tipos del DOM porque no es estándar (Safari no lo tiene). */
interface BeforeInstallPromptEvent extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
}

export function isStandalone(): boolean {
  if (typeof window === 'undefined') return false;
  return (typeof matchMedia === 'function' && matchMedia('(display-mode: standalone)').matches)
    || (navigator as Navigator & { standalone?: boolean }).standalone === true;
}

/**
 * Instalar AlbumFP como WebApp desde un botón, donde el navegador lo permite
 * (Chrome y Edge en Android y escritorio). En iPhone no hay API: la página
 * WebApp enseña los pasos a mano.
 *
 * El evento llega UNA vez, al cargar, en cualquier página; por eso el servicio
 * se crea en la raíz (`App`) y no en la página WebApp, que casi nunca es la
 * primera que se abre.
 */
@Injectable({ providedIn: 'root' })
export class InstallPrompt {
  private deferred: BeforeInstallPromptEvent | null = null;
  readonly canInstall = signal(false);
  readonly installed = signal(isStandalone());

  constructor() {
    if (typeof window === 'undefined') return;
    window.addEventListener('beforeinstallprompt', event => {
      event.preventDefault();
      this.deferred = event as BeforeInstallPromptEvent;
      this.canInstall.set(true);
    });
    window.addEventListener('appinstalled', () => {
      this.deferred = null;
      this.canInstall.set(false);
      this.installed.set(true);
    });
  }

  /** Abre el diálogo del navegador. El evento solo sirve una vez. */
  async install(): Promise<void> {
    const event = this.deferred;
    if (!event) return;
    this.deferred = null;
    this.canInstall.set(false);
    await event.prompt();
    if ((await event.userChoice).outcome === 'accepted') this.installed.set(true);
  }
}
