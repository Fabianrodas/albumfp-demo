import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Confirm } from '../../../core/services/confirm';
import { Modal } from '../modal/modal';

/** Dibuja la confirmación pendiente del servicio `Confirm`. Se monta una sola vez, en el layout. */
@Component({
  selector: 'app-confirm-host',
  imports: [FormsModule, Modal],
  templateUrl: './confirm-host.html',
  styleUrl: './confirm-host.css',
})
export class ConfirmHost {
  readonly confirm = inject(Confirm);
  typed = signal('');

  constructor() {
    // Cada solicitud nueva empieza con el campo vacío; si no, el texto de una
    // confirmación anterior dejaría la siguiente ya habilitada.
    effect(() => { this.confirm.request(); this.typed.set(''); });
  }

  readonly blocked = computed(() => {
    const required = this.confirm.request()?.requireText;
    return !!required && this.typed().trim() !== required;
  });

  cancel = () => this.confirm.resolve(false);
  accept() { if (!this.blocked()) this.confirm.resolve(true); }
}
