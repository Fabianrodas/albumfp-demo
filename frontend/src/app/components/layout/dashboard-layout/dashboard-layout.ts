import { Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { filter, map } from 'rxjs';
import { ConfirmHost } from '../../ui/confirm-host/confirm-host';
import { ToastHost } from '../../ui/toast-host/toast-host';
import { MobileNav } from '../mobile-nav/mobile-nav';
import { Sidebar } from '../sidebar/sidebar';
import { Topbar } from '../topbar/topbar';

/** El detalle de un recuerdo es a pantalla completa en el teléfono: sin barra
 * inferior, como el visor de cualquier galería. Se vuelve con «Volver». */
const IMMERSIVE = /^\/(albumes\/\d+\/media\/\d+|recuerdos\/\d+)/;

@Component({
  selector: 'app-dashboard-layout',
  imports: [RouterOutlet, Sidebar, Topbar, MobileNav, ConfirmHost, ToastHost],
  templateUrl: './dashboard-layout.html',
  styleUrl: './dashboard-layout.css',
})
export class DashboardLayout {
  private readonly router = inject(Router);
  private readonly url = toSignal(
    this.router.events.pipe(filter(e => e instanceof NavigationEnd), map(() => this.router.url)),
    { initialValue: this.router.url });
  readonly immersive = computed(() => IMMERSIVE.test(this.url()));
}
