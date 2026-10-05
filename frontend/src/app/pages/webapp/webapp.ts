import { Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { PublicNav } from '../../components/layout/public-nav/public-nav';
import { PublicFooter } from '../../components/layout/public-footer/public-footer';
import { PublicBackdrop } from '../../components/ui/public-backdrop/public-backdrop';
import { ShotFrame } from '../../components/ui/shot-frame/shot-frame';
import { Icon, IconName } from '../../components/ui/icon/icon';
import { ScrollReveal } from '../../core/directives/scroll-reveal';
import { InstallPrompt } from '../../core/services/install-prompt';

interface Ventaja { icon: IconName; titulo: string; texto: string }
interface Paso { titulo: string; texto: string; icon?: IconName; boton?: string }
interface Pregunta { q: string; a: string }

/** Pantallas del teléfono, capturadas de la app en marcha (cuenta de demostración)
 * a 390x844, en los dos temas: `capturas/movil/*` y `capturas/claro/movil/*`. */
export const CAPTURAS_MOVIL = ['inicio', 'biblioteca', 'album', 'recuerdo'] as const;

/**
 * «WebApp»: qué es instalar AlbumFP en el teléfono y cómo hacerlo.
 *
 * Lo que se promete aquí es lo que la app de verdad hace (L16): el service
 * worker solo guarda la interfaz, nunca fotos ni la sesión, y los avisos
 * siguen siendo in-app, sin notificaciones push.
 */
@Component({
  selector: 'app-webapp',
  imports: [RouterLink, PublicBackdrop, PublicNav, PublicFooter, ShotFrame, Icon, ScrollReveal],
  templateUrl: './webapp.html',
  styleUrl: './webapp.css',
})
export class WebApp {
  readonly installer = inject(InstallPrompt);

  readonly ventajas: Ventaja[] = [
    { icon: 'smartphone', titulo: 'Se abre como una app', texto: 'A pantalla completa, sin barras del navegador, con su propio icono y su lugar entre tus apps.' },
    { icon: 'sparkle', titulo: 'Siempre al día', texto: 'No pasa por ninguna tienda ni pide actualizaciones: cada vez que la abres ya es la última versión.' },
    { icon: 'lock', titulo: 'Tus fotos no se copian al teléfono', texto: 'La app solo guarda su interfaz para abrir rápido. Tus recuerdos siguen en tu cuenta, protegidos igual que en la web.' },
    { icon: 'download', titulo: 'Casi no ocupa espacio', texto: 'Pesa lo que una página web. No hay nada que descargar de una tienda ni permisos que conceder.' },
  ];

  readonly pasosIphone: Paso[] = [
    { titulo: 'Abre AlbumFP Demo en Safari', texto: 'La instalación móvil no está disponible desde un teléfono separado: el servidor local solo acepta conexiones de este equipo.' },
    { titulo: 'Toca Compartir', texto: 'Es el cuadrado con una flecha hacia arriba, en la barra de Safari.', icon: 'share-ios', boton: 'Compartir' },
    { titulo: 'Elige «Añadir a pantalla de inicio»', texto: 'Desliza la lista de opciones hacia abajo si no la ves a la primera.', icon: 'plus-square', boton: 'Añadir a pantalla de inicio' },
    { titulo: 'Confirma con «Añadir»', texto: 'El icono de AlbumFP aparece en tu pantalla de inicio. Ábrela desde ahí.' },
  ];

  readonly pasosAndroid: Paso[] = [
    { titulo: 'Abre AlbumFP Demo en Chrome', texto: 'Usa Chrome en el mismo equipo donde corre el servidor local. Si aparece «Instalar AlbumFP», el navegador puede añadirla a este equipo.' },
    { titulo: 'Abre el menú', texto: 'Son los tres puntos de la esquina superior derecha.', icon: 'more-vertical', boton: 'Menú' },
    { titulo: 'Elige «Instalar aplicación»', texto: 'En algunos teléfonos se llama «Añadir a pantalla de inicio».', icon: 'download', boton: 'Instalar aplicación' },
    { titulo: 'Confirma con «Instalar»', texto: 'AlbumFP queda entre tus aplicaciones, con su icono, lista para abrir.' },
  ];

  readonly preguntas: Pregunta[] = [
    { q: '¿Tengo que descargarla de una tienda de aplicaciones?', a: 'No. Se instala desde el navegador en unos segundos, sin App Store ni Google Play, y sin crear una cuenta nueva: entras con la de siempre.' },
    { q: '¿Funciona sin conexión?', a: 'La app abre sin conexión, pero tus fotos y álbumes viven en tu cuenta, así que para verlos necesitas internet. Nada de tu biblioteca se guarda en el teléfono.' },
    { q: '¿Recibiré notificaciones en el teléfono?', a: 'No. AlbumFP no envía notificaciones push: los avisos (invitaciones, fotos nuevas en álbumes compartidos) aparecen en la campana de la app cuando la abres.' },
    { q: '¿Es tan segura como la web?', a: 'Es la misma aplicación, con la misma sesión protegida y los mismos permisos. Instalarla no da acceso a tus fotos del teléfono: solo subes lo que tú eliges.' },
    { q: '¿Cómo se actualiza?', a: 'Sola. Cuando hay una versión nueva, la app la usa la siguiente vez que la abres.' },
    { q: '¿Cómo la desinstalo?', a: 'Como cualquier app: mantén pulsado su icono y elige eliminarla. Tu cuenta y tus fotos no se tocan.' },
    { q: '¿Puedo abrir esta Demo desde mi teléfono?', a: 'No. La Demo enlaza sus servicios a localhost en el equipo donde se ejecuta. Las capturas muestran el diseño móvil, pero no se habilita acceso desde otros dispositivos.' },
  ];

  install() { void this.installer.install(); }
}
