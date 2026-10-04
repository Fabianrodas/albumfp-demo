import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { ConfirmHost } from '../../ui/confirm-host/confirm-host';
import { ToastHost } from '../../ui/toast-host/toast-host';
import { Sidebar } from '../sidebar/sidebar';
import { Topbar } from '../topbar/topbar';

@Component({
  selector: 'app-dashboard-layout',
  imports: [RouterOutlet, Sidebar, Topbar, ConfirmHost, ToastHost],
  templateUrl: './dashboard-layout.html',
  styleUrl: './dashboard-layout.css',
})
export class DashboardLayout {}
