import { Component, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { InstallPrompt } from './core/services/install-prompt';
import { Theme } from './core/services/theme';

@Component({ selector: 'app-root', imports: [RouterOutlet], template: '<router-outlet />' })
export class App {
  // Eagerly instantiate the service so a persisted theme also applies on landing/auth pages.
  private readonly theme = inject(Theme);
  // `beforeinstallprompt` fires once, on whatever page loads first: listen from the root.
  private readonly installPrompt = inject(InstallPrompt);
}
