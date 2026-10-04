import { Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AlbumApi, Media } from '../../../core/services/album-api';
import { MediaGrid } from '../../../components/ui/media-grid/media-grid';
import { Icon } from '../../../components/ui/icon/icon';
import { ScrollReveal } from '../../../core/directives/scroll-reveal';

@Component({ selector: 'app-favorites', imports: [MediaGrid, RouterLink, Icon, ScrollReveal], templateUrl: './favorites.html', styleUrl: './favorites.css' })
export class Favorites {
  private readonly api = inject(AlbumApi);
  favorites = signal<Media[]>([]);
  /** El estado vacío espera a la primera respuesta para no parpadear. */
  loaded = signal(false);
  error = signal('');

  ngOnInit() {
    this.api.favorites().subscribe({
      next: r => { this.favorites.set(r.data); this.loaded.set(true); },
      error: error => {
        this.error.set(error?.error?.message || 'No se pudieron cargar los favoritos.');
        this.loaded.set(true);
      },
    });
  }
}
