import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Icon } from '../../ui/icon/icon';
import { ScrollReveal } from '../../../core/directives/scroll-reveal';
import { AlbumApi, UserSummary } from '../../../core/services/album-api';

const PER_PAGE = 18;

/** Personas: el directorio de cuentas. Fue la página `/usuarios`; desde F01
 * vive dentro de Inicio como una sección secundaria que se despliega a
 * demanda (la URL vieja llega con `?panel=personas` y la abre). Misma API y
 * mismas reglas de siempre: solo nombre, usuario y álbumes públicos. */
@Component({
  selector: 'app-people-directory',
  imports: [RouterLink, FormsModule, Icon, ScrollReveal],
  templateUrl: './people-directory.html',
  styleUrl: './people-directory.css',
})
export class PeopleDirectory {
  private readonly api = inject(AlbumApi);

  users = signal<UserSummary[]>([]);
  page = signal(1);
  totalPages = signal(0);
  loaded = signal(false);
  error = signal('');
  query = '';

  ngOnInit() { this.load(); }

  avatarUrl(id: number) { return this.api.avatarUrl(id); }
  initials(username: string) { return username.slice(0, 2).toUpperCase(); }

  albumLabel(count: number) {
    if (!count) return 'Sin álbumes públicos';
    return count === 1 ? '1 álbum público' : `${count} álbumes públicos`;
  }

  search() {
    this.page.set(1);
    this.load();
  }

  clearSearch() {
    this.query = '';
    this.search();
  }

  goTo(page: number) {
    if (page < 1 || (this.totalPages() && page > this.totalPages())) return;
    this.page.set(page);
    this.load();
  }

  private load() {
    this.error.set('');
    const params: Record<string, string> = { page: String(this.page()), per_page: String(PER_PAGE) };
    const q = this.query.trim();
    if (q) params['q'] = q;

    this.api.users(params).subscribe({
      next: response => {
        this.users.set(response.data);
        this.totalPages.set(response.meta?.pagination?.total_pages ?? 0);
        this.loaded.set(true);
      },
      error: error => {
        this.error.set(error?.error?.message || 'No se pudieron cargar los usuarios.');
        this.loaded.set(true);
      },
    });
  }
}
