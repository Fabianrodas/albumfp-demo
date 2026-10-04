import { Component, computed, effect, input, signal } from '@angular/core';
import { toDataURL } from 'qrcode';

/**
 * Código QR de un enlace ya creado, generado **en el propio navegador**.
 *
 * No hay ninguna llamada a un servicio de QR: mandar el enlace a un tercero
 * para que dibuje el cuadrito sería entregarle el token de acceso al álbum,
 * que es justo lo que un enlace privado no puede permitirse. La librería
 * (`qrcode`) trabaja en memoria y devuelve un data URL.
 *
 * Solo recibe el enlace: no conoce el álbum, ni la sesión, ni la API.
 */
@Component({
  selector: 'app-share-qr',
  templateUrl: './share-qr.html',
  styleUrl: './share-qr.css',
})
export class ShareQr {
  link = input.required<string>();
  /** Nombre del archivo al descargar. Nunca lleva el token: acaba en la carpeta
   * de descargas y en el historial del navegador. */
  filename = input('codigo-qr');

  readonly dataUrl = signal('');
  readonly error = signal('');

  /** Nombre de archivo seguro a partir del título del álbum. */
  readonly downloadName = computed(() => {
    const base = this.filename()
      .normalize('NFD').replace(/[̀-ͯ]/g, '')
      .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
    return `${base || 'codigo-qr'}.png`;
  });

  constructor() {
    effect(() => {
      const url = this.link();
      this.error.set('');
      this.dataUrl.set('');
      if (!url) return;
      // Fondo blanco fijo, también en modo oscuro: un QR sobre papel oscuro
      // se lee mal o no se lee. El margen es la zona de silencio que la
      // especificación exige alrededor del código.
      toDataURL(url, { errorCorrectionLevel: 'M', margin: 2, width: 220, color: { dark: '#111111', light: '#ffffff' } })
        .then(imagen => this.dataUrl.set(imagen))
        .catch(() => this.error.set('No pudimos generar el código.'));
    });
  }
}
