import { HttpInterceptorFn } from '@angular/common/http';
import { sessionHint } from '../services/session-hint';

/**
 * Desde S01 la credencial ya NO la pone este interceptor: viaja sola, en una
 * cookie `HttpOnly` que el navegador adjunta a cada petición al mismo origen.
 * Lo único que queda por añadir es la cabecera CSRF, y solo donde hace falta.
 *
 * Solo en métodos que cambian estado: un GET no muta nada, y exigirle la
 * cabecera obligaría a tener sesión resuelta para leer un álbum público.
 * Solo a URLs relativas: una absoluta apunta fuera y mandarle el token CSRF
 * sería entregárselo a un tercero.
 */
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  if (/^https?:\/\//i.test(req.url)) return next(req);
  // L16: toda petición de la app (API, sesión, media privada) pasa de largo
  // del service worker: ni la ve, ni la puede cachear, y el progreso de
  // subida lo sigue midiendo el navegador. El worker solo sirve la cáscara.
  req = req.clone({ setHeaders: { 'ngsw-bypass': 'true' } });
  if (SAFE_METHODS.has(req.method)) return next(req);

  const csrf = sessionHint.csrf();
  return next(csrf ? req.clone({ setHeaders: { 'X-CSRF-Token': csrf } }) : req);
};
