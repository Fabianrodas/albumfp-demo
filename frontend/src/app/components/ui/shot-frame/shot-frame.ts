import { Component, input } from '@angular/core';

/**
 * Marco editorial de una captura real del producto: papel, filo cálido y sombra
 * tintada, con la proporción declarada para que el hueco esté reservado antes de
 * que la imagen llegue.
 *
 * No lleva movimiento de ningún tipo, a propósito: las vistas públicas se
 * quedaron sin animaciones para que la carga y el scroll sean lo más rápidos
 * posible.
 *
 * `zoom` y `crop` acercan el encuadre a una zona de la pantalla; siguen siendo
 * el archivo real, nunca un montaje.
 */
@Component({
  selector: 'app-shot-frame',
  templateUrl: './shot-frame.html',
  styleUrl: './shot-frame.css',
})
export class ShotFrame {
  src = input.required<string>();
  alt = input.required<string>();
  /** Proporción del hueco. Las capturas nativas son 4:3. */
  ratio = input('4 / 3');
  /**
   * Acercamiento sobre la captura. Las pantallas con diálogo traen medio
   * archivo en penumbra detrás; un `zoom` de 1.5 deja el diálogo llenando el
   * marco. Escala en proporción, así que encuadra igual en cualquier ancho.
   */
  zoom = input(1);
  /** Punto de la captura sobre el que se centra el encuadre. */
  crop = input('center');
  /** La del hero: se carga con prioridad en vez de en diferido. */
  priority = input(false);
}
