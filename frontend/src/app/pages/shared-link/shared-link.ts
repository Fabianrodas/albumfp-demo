import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { Icon } from '../../components/ui/icon/icon';
import { MediaGrid } from '../../components/ui/media-grid/media-grid';
import { AlbumApi, Media, Share } from '../../core/services/album-api';
import { Theme } from '../../core/services/theme';

@Component({
  selector: 'app-shared-link',
  imports: [MediaGrid, FormsModule, Icon],
  templateUrl: './shared-link.html',
  styleUrl: './shared-link.css',
})
export class SharedLink {
  readonly theme = inject(Theme);
  private readonly api = inject(AlbumApi);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  token = this.route.snapshot.paramMap.get('token') || '';
  title = signal('Álbum compartido');
  description = signal('');
  share = signal<Share | null>(null);
  media = signal<Media[]>([]);
  error = signal('');
  /** Distinto de `error`: un enlace con contraseña no es un enlace roto,
   * solo falta un paso antes de poder verlo. */
  locked = signal(false);
  password = '';
  unlocking = signal(false);
  unlockError = signal('');

  ngOnInit(){ this.load(); }

  load(){
    this.locked.set(false);
    this.error.set('');
    this.api.sharedLink(this.token).subscribe({
      next: response => {
        this.title.set(response.data.album.titulo);
        this.description.set(response.data.album.descripcion || '');
        this.share.set(response.data.share);
        this.media.set(response.data.media);
      },
      error: error => {
        if (error?.error?.code === 'share_password_required') { this.locked.set(true); return; }
        this.error.set(error?.error?.message || 'Este enlace no está disponible.');
      },
    });
  }

  unlock() {
    if (!this.password || this.unlocking()) return;
    this.unlocking.set(true);
    this.unlockError.set('');
    this.api.unlockSharedLink(this.token, this.password).subscribe({
      next: () => { this.unlocking.set(false); this.password = ''; this.load(); },
      error: error => {
        this.unlocking.set(false);
        this.unlockError.set(error?.error?.message || 'No se pudo comprobar la contraseña.');
      },
    });
  }

  /**
   * Abrir una foto lleva al detalle COMPLETO (`/enlace/:token/media/:id`), el
   * mismo componente que ve una cuenta con acceso de solo lectura: pestañas de
   * Recuerdo, Ficha, Tags y Texto, sin una sola acción de escritura.
   *
   * Antes esto abría un visor propio en un modal, que solo enseñaba la foto y
   * su descripción. Se retiró entero en vez de dejarlo al lado: mantener dos
   * visores era justo lo que hacía que el enlace público se viera distinto.
   */
  openMedia(item: Media) {
    this.router.navigate(['/enlace', this.token, 'media', item.id]);
  }
}
