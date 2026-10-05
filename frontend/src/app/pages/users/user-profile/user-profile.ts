import { Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { PublicAlbumCard } from '../../../components/ui/public-album-card/public-album-card';
import { ScrollReveal } from '../../../core/directives/scroll-reveal';
import { readReturnTo } from '../../../core/utils/return-to';
import { AlbumApi, PublicAlbum, UserProfile as PublicUser } from '../../../core/services/album-api';

const PER_PAGE = 12;

/** Perfil público de otra persona: quién es y qué tiene publicado. */
@Component({
  selector: 'app-user-profile',
  imports: [PublicAlbumCard, ScrollReveal, RouterLink],
  templateUrl: './user-profile.html',
  styleUrl: './user-profile.css',
})
export class UserProfile {
  private readonly api = inject(AlbumApi);
  private readonly route = inject(ActivatedRoute);

  profile = signal<PublicUser | null>(null);
  albums = signal<PublicAlbum[]>([]);
  page = signal(1);
  totalPages = signal(0);
  loaded = signal(false);
  notFound = signal(false);
  private username = '';

  /** A dónde vuelve «Regresar al post» (v1.1). Llega como estado de navegación
   * desde el autor de un comentario, así que no viaja en la URL y sobrevive a
   * recargar (`history.state`). Solo rutas de esta app: nada que empiece por
   * `//` o por un esquema. Sin estado, no hay enlace. */
  private readonly router = inject(Router);
  private readonly returnState = readReturnTo(this.router.currentNavigation()?.extras.state ?? history.state);
  /* Árbol ya parseado: un `routerLink` con texto codificaría el `?` de la vuelta. */
  readonly returnTo = this.returnState && { tree: this.router.parseUrl(this.returnState.back), label: this.returnState.label };

  readonly initials = computed(() => (this.profile()?.username || '').slice(0, 2).toUpperCase());

  readonly memberSince = computed(() => {
    const raw = this.profile()?.created_at;
    if (!raw) return '—';
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return '—';
    return new Intl.DateTimeFormat('es', { month: 'long', year: 'numeric' }).format(date);
  });

  ngOnInit() {
    // El parámetro se observa, no se lee una vez: navegar de un perfil a otro
    // reutiliza el componente y no volvería a cargar nada.
    this.route.paramMap.subscribe(params => {
      this.username = params.get('username') || '';
      this.page.set(1);
      this.loaded.set(false);
      this.notFound.set(false);
      this.load();
    });
  }

  avatarUrl() {
    const id = this.profile()?.id;
    return id ? this.api.avatarUrl(id) : '';
  }

  goTo(page: number) {
    if (page < 1 || (this.totalPages() && page > this.totalPages())) return;
    this.page.set(page);
    this.loadAlbums();
  }

  private load() {
    this.api.userProfile(this.username).subscribe({
      next: response => {
        this.profile.set(response.data);
        this.loadAlbums();
      },
      error: () => {
        this.notFound.set(true);
        this.loaded.set(true);
      },
    });
  }

  private loadAlbums() {
    this.api.userAlbums(this.username, { page: String(this.page()), per_page: String(PER_PAGE) }).subscribe({
      next: response => {
        this.albums.set(response.data);
        this.totalPages.set(response.meta?.pagination?.total_pages ?? 0);
        this.loaded.set(true);
      },
      error: () => this.loaded.set(true),
    });
  }
}
