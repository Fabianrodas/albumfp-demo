import { Injectable, signal } from '@angular/core';

export interface ConfirmRequest {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Pinta el botón de confirmar como destructivo. */
  danger?: boolean;
  /**
   * Exige escribir exactamente este texto antes de poder confirmar. Lo usa
   * "Eliminar mi cuenta", que pide el nombre de usuario.
   */
  requireText?: string;
}

/**
 * Confirmaciones con los estilos de la aplicación, en lugar de `window.confirm`,
 * que no se puede estilar. Un único host montado en el layout las dibuja, así
 * que ninguna página necesita su propio estado de diálogo.
 */
@Injectable({ providedIn: 'root' })
export class Confirm {
  readonly request = signal<ConfirmRequest | null>(null);
  private settle?: (value: boolean) => void;

  ask(request: ConfirmRequest): Promise<boolean> {
    // Una confirmación pendiente se resuelve como cancelada antes de abrir la
    // siguiente, para que su promesa nunca quede colgada.
    this.settle?.(false);
    this.request.set(request);
    return new Promise<boolean>(resolve => { this.settle = resolve; });
  }

  resolve(confirmed: boolean) {
    this.request.set(null);
    const settle = this.settle;
    this.settle = undefined;
    settle?.(confirmed);
  }
}
