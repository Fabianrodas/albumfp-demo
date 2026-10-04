import { Component, input, output } from '@angular/core';
import { DialogTrap } from '../../../core/directives/dialog-trap';
import { Icon } from '../icon/icon';

/**
 * Diálogo propio de la aplicación. Reemplaza a los cuadros del navegador, que
 * no se pueden estilar. El foco atrapado, Escape y la devolución del foco
 * vienen de la directiva `appDialogTrap`, que ya existía para el panel de
 * subida y el de compartir.
 *
 * El contenido va por proyección; las acciones del pie con `[modal-actions]`.
 */
@Component({
  selector: 'app-modal',
  imports: [DialogTrap, Icon],
  templateUrl: './modal.html',
  styleUrl: './modal.css',
})
export class Modal {
  title = input('');
  open = input(false);
  /** `sm` para confirmaciones, `md` para formularios, `lg` para compartir,
   * `xl` para ver una foto en grande. */
  size = input<'sm' | 'md' | 'lg' | 'xl'>('md');
  /** Un diálogo destructivo no se cierra al hacer clic fuera, para no perder lo escrito por accidente. */
  dismissable = input(true);
  closed = output<void>();

  readonly close = () => this.closed.emit();

  onBackdrop() {
    if (this.dismissable()) this.close();
  }
}
