import { Component, inject } from '@angular/core';
import { Toast } from '../../../core/services/toast';
import { Icon } from '../icon/icon';

@Component({
  selector: 'app-toast-host',
  imports: [Icon],
  templateUrl: './toast-host.html',
  styleUrl: './toast-host.css',
})
export class ToastHost {
  readonly toast = inject(Toast);
}
