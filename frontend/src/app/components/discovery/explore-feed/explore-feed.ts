import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { PublicMediaCard } from '../../ui/public-media-card/public-media-card';
import { ScrollReveal } from '../../../core/directives/scroll-reveal';
import { AlbumApi, PublicMedia } from '../../../core/services/album-api';

const PER_PAGE = 12;

/** Explorar: el feed comunitario de fotos públicas. Fue página propia
 * (`/explorar`, P08) y desde F01 es el contenido principal de Inicio; la URL
 * vieja redirige ahí. Qué es público lo decide el backend, no este componente. */
@Component({
  selector: 'app-explore-feed',
  imports: [FormsModule, PublicMediaCard, ScrollReveal],
  templateUrl: './explore-feed.html',
  styleUrl: './explore-feed.css',
})
export class ExploreFeed {
  private readonly api = inject(AlbumApi);
  media = signal<PublicMedia[]>([]);
  page = signal(1);
  totalPages = signal(0);
  loaded = signal(false);
  error = signal('');
  sortBy: 'created_at' | 'taken_at' = 'created_at';
  sortDir: 'desc' | 'asc' = 'desc';

  ngOnInit() { this.load(); }

  load() {
    this.error.set('');
    this.api.publicMedia({
      page: String(this.page()), per_page: String(PER_PAGE),
      sort_by: this.sortBy, sort_dir: this.sortDir,
    }).subscribe({
      next: response => {
        this.media.set(response.data);
        this.totalPages.set(response.meta?.pagination?.total_pages ?? 0);
        this.loaded.set(true);
      },
      error: error => {
        this.error.set(error?.error?.message || 'No se pudieron cargar las fotos públicas.');
        this.loaded.set(true);
      },
    });
  }

  changeSort() { this.page.set(1); this.load(); }

  goTo(page: number) {
    if (page < 1 || (this.totalPages() && page > this.totalPages())) return;
    this.page.set(page);
    this.load();
    document.getElementById('public-feed-title')?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  }
}
