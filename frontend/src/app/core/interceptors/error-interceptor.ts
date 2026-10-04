import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { Auth } from '../services/auth';

/**
 * Un 401 significa lo mismo que siempre —la sesión no vale— pero desde S01 no
 * hay nada que reintentar: el servidor decide si sigue viva y no existe un
 * refresh token que canjear. Se limpia lo que se pinta y se manda al login.
 *
 * Se llama a `clearLocalState()` y no a `logout()`: pedirle al servidor que
 * revoque una sesión que él acaba de rechazar sería una petición garantizada
 * a fallar, y encima en bucle si el 401 vino del propio `/auth/logout`.
 */
export const errorInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(Auth);
  const router = inject(Router);

  return next(req).pipe(catchError((error: HttpErrorResponse) => {
    const esRutaDeEntrada = req.url.startsWith('/auth/login') || req.url.startsWith('/auth/register');
    // `/media` y `/unlock` de un enlace público (S08) no llevan sesión --
    // un 401 ahí es "falta la contraseña del enlace", no "tu sesión expiró".
    // `/claim` SÍ exige sesión de cuenta real, así que no entra aquí.
    const esEnlacePublico = /^\/api\/shared\/[^/]+\/(media|unlock)(\/|$)/.test(req.url);
    if (error.status === 401 && !esRutaDeEntrada && !esEnlacePublico) {
      auth.clearLocalState();
      // Estando YA en el login no se navega: hacerlo reejecuta el guard de
      // invitado, que vuelve a resolver la sesion, que vuelve a dar 401.
      if (!router.url.startsWith('/login')) {
        router.navigate(['/login'], { queryParams: { returnUrl: router.url } });
      }
    }
    return throwError(() => error);
  }));
};
