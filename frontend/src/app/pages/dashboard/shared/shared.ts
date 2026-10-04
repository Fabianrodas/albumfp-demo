import { Component, computed, inject } from '@angular/core';
import { Album, AlbumApi } from '../../../core/services/album-api';
import { pagedList } from '../../../core/utils/paged-list';
import { AlbumCard } from '../../../components/ui/album-card/album-card';
import { ScrollReveal } from '../../../core/directives/scroll-reveal';

@Component({selector:'app-shared',imports:[AlbumCard,ScrollReveal],templateUrl:'./shared.html',styleUrl:'./shared.css'})
export class Shared {
  private readonly api = inject(AlbumApi);
  /** P01: dos listas paginadas, una por rol. Antes se pedian con per_page=100
   *  cada una y sin continuacion, asi que quien tuviera mas de 100 albumes
   *  compartidos no podia llegar al resto. */
  private readonly readPages = pagedList<Album>((page, perPage) =>
    this.api.albums({ role: 'read', page: String(page), per_page: String(perPage) }));
  private readonly writePages = pagedList<Album>((page, perPage) =>
    this.api.albums({ role: 'write', page: String(page), per_page: String(perPage) }));

  readonly readAlbums = this.readPages.items;
  readonly writeAlbums = this.writePages.items;
  readonly totalRead = this.readPages.total;
  readonly totalWrite = this.writePages.total;
  readonly loadingRead = this.readPages.loading;
  readonly loadingWrite = this.writePages.loading;
  hasMoreRead() { return this.readPages.hasMore(); }
  hasMoreWrite() { return this.writePages.hasMore(); }
  loadMoreRead() { this.readPages.loadMore(); }
  loadMoreWrite() { this.writePages.loadMore(); }

  /** Derivados, no sondeados: la pantalla deja de cargar cuando las DOS
   *  primeras paginas han vuelto, y el estado vacio no parpadea porque una
   *  llegue antes que la otra. */
  readonly loading = computed(() => !(this.readPages.loaded() && this.writePages.loaded()));
  readonly error = computed(() => this.readPages.error() || this.writePages.error());

  ngOnInit() {
    this.readPages.reset();
    this.writePages.reset();
  }
}
