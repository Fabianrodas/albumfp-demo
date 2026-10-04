import { Component } from '@angular/core';

/**
 * Aurora de las cinco vistas públicas: cuatro campos de verde que cubren el
 * viewport, se solapan y se funden entre sí sobre el crema del papel.
 *
 * Es UNA sola capa fija detrás de todo el documento, no una por sección. Al
 * estar `fixed` no se desplaza con el scroll, así que no hay nada que se pueda
 * repetir ni ningún corte entre secciones.
 *
 * Los dos `span` son los campos que se MUEVEN; los otros dos viven quietos en
 * el `background` del host, donde no cuestan un frame. Cada capa animada sobre
 * el viewport cuesta unos 8 fps, así que solo se pagan las que aportan.
 *
 * Los `span` no llevan contenido ni semántica: el host es `aria-hidden`. Todo lo demás vive en el CSS — sin
 * JavaScript, sin escuchar el scroll ni el puntero.
 */
@Component({
  selector: 'app-public-backdrop',
  template: '<span class="campo campo--a"></span><span class="campo campo--b"></span>',
  styleUrl: './public-backdrop.css',
  host: { 'aria-hidden': 'true' },
})
export class PublicBackdrop {}
