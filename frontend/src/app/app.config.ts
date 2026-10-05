import { ApplicationConfig, LOCALE_ID, isDevMode, provideBrowserGlobalErrorListeners } from '@angular/core';
import { registerLocaleData } from '@angular/common';
import localeEs from '@angular/common/locales/es';
import { provideServiceWorker } from '@angular/service-worker';
import { provideRouter, withInMemoryScrolling, withViewTransitions } from '@angular/router';
import { provideHttpClient, withInterceptors, withXhr } from '@angular/common/http';
import { authInterceptor } from './core/interceptors/auth-interceptor';
import { errorInterceptor } from './core/interceptors/error-interceptor';

import { routes } from './app.routes';

// F06: toda fecha de DatePipe en español («24 sept 2026»), como el resto de la app.
registerLocaleData(localeEs);

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    { provide: LOCALE_ID, useValue: 'es' },
    provideRouter(
      routes,
      withInMemoryScrolling({ scrollPositionRestoration: 'top' }),
      withViewTransitions({ skipInitialTransition: true }),
    ),
    // v1.1: XHR explícito. Desde Angular 22 `provideHttpClient()` usa Fetch por
    // defecto, y Fetch NO emite progreso de subida: cada archivo grande se
    // quedaba en 0% hasta terminar de golpe (el fallo reportado en v1.0.0).
    provideHttpClient(withXhr(), withInterceptors([authInterceptor, errorInterceptor])),
    // L16: solo cachea la cáscara estática (ngsw-config.json, sin dataGroups).
    // Nada de push: ni suscripción ni permiso de notificaciones (L12 es in-app).
    provideServiceWorker('ngsw-worker.js', { enabled: !isDevMode(), registrationStrategy: 'registerWhenStable:30000' }),
  ]
};
