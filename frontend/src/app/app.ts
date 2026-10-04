import { Component, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { Theme } from './core/services/theme';

@Component({ selector: 'app-root', imports: [RouterOutlet], template: '<router-outlet />' })
export class App {
  // Eagerly instantiate the service so a persisted theme also applies on landing/auth pages.
  private readonly theme = inject(Theme);
}
