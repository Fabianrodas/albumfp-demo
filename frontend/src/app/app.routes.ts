import { inject } from '@angular/core';
import { Router, Routes } from '@angular/router';
import { authChildGuard, authGuard } from './core/guards/auth-guard';
import { guestGuard } from './core/guards/guest-guard';
import { AuthLayout } from './components/auth/auth-layout/auth-layout';
import { DashboardLayout } from './components/layout/dashboard-layout/dashboard-layout';
import { Landing } from './pages/landing/landing';

/**
 * Cada página se descarga cuando se visita, no al abrir la app (`loadComponent`,
 * de Angular, sin ninguna librería de por medio). Antes todo iba en el paquete
 * inicial: quien entraba a la portada se bajaba también el álbum, el visor, el
 * selector de mapa y el generador de QR sin haber iniciado sesión siquiera.
 *
 * Se quedan en el paquete inicial solo las tres piezas que se necesitan sí o sí
 * para pintar lo primero: la portada, y las dos cáscaras que envuelven al resto
 * de rutas (si se cargaran aparte añadirían un salto extra antes de cada vista).
 */
export const routes: Routes = [
  { path: '', component: Landing, title: 'AlbumFP' },
  { path: 'acerca-de', loadComponent: () => import('./pages/about/about').then(m => m.About), title: 'Acerca de - AlbumFP' },
  { path: 'como-funciona', loadComponent: () => import('./pages/how-it-works/how-it-works').then(m => m.HowItWorks), title: 'Cómo funciona - AlbumFP' },
  { path: 'webapp', loadComponent: () => import('./pages/webapp/webapp').then(m => m.WebApp), title: 'WebApp - AlbumFP' },
  {
    path: '', component: AuthLayout, canActivate: [guestGuard], children: [
      { path: 'login', loadComponent: () => import('./pages/auth/login/login').then(m => m.Login), title: 'Iniciar sesión - AlbumFP' },
      { path: 'registro', loadComponent: () => import('./pages/auth/register/register').then(m => m.Register), title: 'Regístrate - AlbumFP' },
      { path: 'recuperar', loadComponent: () => import('./pages/auth/recover/recover').then(m => m.Recover), title: 'Recuperar cuenta - AlbumFP' },
    ]
  },
  { path: 'enlace/:token', loadComponent: () => import('./pages/shared-link/shared-link').then(m => m.SharedLink), title: 'Álbum compartido - AlbumFP' },
  // El visitante de un enlace abre el MISMO detalle que una cuenta con acceso
  // de solo lectura. Va fuera del dashboard (no hay sesión ni cáscara) pero
  // reutiliza el componente entero: el servidor le manda role=read sin
  // capacidades, así que ninguna acción de escritura llega a pintarse.
  { path: 'enlace/:token/media/:mediaId', loadComponent: () => import('./pages/dashboard/media-detail/media-detail').then(m => m.MediaDetail), title: 'Recuerdo compartido - AlbumFP' },
  { path: 'invitacion/:token', loadComponent: () => import('./pages/share-claim/share-claim').then(m => m.ShareClaim), title: 'Aceptar invitación - AlbumFP' },
  {
    path: '', component: DashboardLayout, canActivate: [authGuard], canActivateChild: [authChildGuard], children: [
      { path: 'inicio', loadComponent: () => import('./pages/dashboard/home/home').then(m => m.Home), title: 'Inicio - AlbumFP' },
      { path: 'biblioteca', loadComponent: () => import('./pages/dashboard/library/library').then(m => m.Library), title: 'Biblioteca - AlbumFP' },
      { path: 'albumes', loadComponent: () => import('./pages/dashboard/albums/albums').then(m => m.Albums), title: 'Álbumes - AlbumFP' },
      { path: 'lugares', loadComponent: () => import('./pages/dashboard/places/places').then(m => m.Places), title: 'Lugares - AlbumFP' },
      // L11: literal y ANTES de `albumes/:id`, para que un álbum inteligente
      // nunca se confunda con un álbum normal ni use su id.
      { path: 'albumes/inteligentes/:id', loadComponent: () => import('./pages/dashboard/smart-album-detail/smart-album-detail').then(m => m.SmartAlbumDetail), title: 'Álbum inteligente - AlbumFP' },
      { path: 'albumes/:albumId/media/:mediaId', loadComponent: () => import('./pages/dashboard/media-detail/media-detail').then(m => m.MediaDetail), title: 'Recuerdo - AlbumFP' },
      { path: 'albumes/:id', loadComponent: () => import('./pages/dashboard/album-detail/album-detail').then(m => m.AlbumDetail), title: 'Álbum - AlbumFP' },
      // L10B: un recuerdo sin álbum de contexto (p. ej. suelto tras quitarlo de su último álbum).
      { path: 'recuerdos/:mediaId', loadComponent: () => import('./pages/dashboard/media-detail/media-detail').then(m => m.MediaDetail), title: 'Recuerdo - AlbumFP' },
      { path: 'favoritos', loadComponent: () => import('./pages/dashboard/favorites/favorites').then(m => m.Favorites), title: 'Favoritos - AlbumFP' },
      { path: 'compartido', loadComponent: () => import('./pages/dashboard/shared/shared').then(m => m.Shared), title: 'Compartido conmigo - AlbumFP' },
      { path: 'archivo', loadComponent: () => import('./pages/dashboard/library/library').then(m => m.Library), data: { archived: true }, title: 'Archivo - AlbumFP' },
      { path: 'papelera', loadComponent: () => import('./pages/dashboard/trash/trash').then(m => m.Trash), title: 'Papelera - AlbumFP' },
      { path: 'perfil', loadComponent: () => import('./pages/dashboard/profile/profile').then(m => m.Profile), title: 'Mi perfil - AlbumFP' },
      // F01: Explorar y Usuarios viven dentro de Inicio. Las URLs viejas no se
      // rompen: redirigen a la ÚNICA vista canónica (nada de dos copias con
      // estado propio), y /usuarios llega con el panel de personas abierto.
      { path: 'explorar', redirectTo: 'inicio', pathMatch: 'full' },
      { path: 'usuarios', pathMatch: 'full', redirectTo: () => inject(Router).createUrlTree(['/inicio'], { queryParams: { panel: 'personas' } }) },
      // Después de las rutas literales del dashboard, para que no las eclipse.
      { path: 'u/:username', loadComponent: () => import('./pages/users/user-profile/user-profile').then(m => m.UserProfile), title: 'Perfil - AlbumFP' },
    ]
  },
  { path: '**', loadComponent: () => import('./pages/not-found/not-found').then(m => m.NotFound), title: 'Página no encontrada - AlbumFP' },
];
