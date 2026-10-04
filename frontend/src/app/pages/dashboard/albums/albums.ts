import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule, ReactiveFormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { AlbumCard } from '../../../components/ui/album-card/album-card';
import { Icon } from '../../../components/ui/icon/icon';
import { MediaGrid } from '../../../components/ui/media-grid/media-grid';
import { Modal } from '../../../components/ui/modal/modal';
import { SearchFilterFields } from '../../../components/ui/search-filter-fields/search-filter-fields';
import { SmartAlbumCard } from '../../../components/ui/smart-album-card/smart-album-card';
import { SmartAlbumEditor } from '../../../components/ui/smart-album-editor/smart-album-editor';
import { ScrollReveal } from '../../../core/directives/scroll-reveal';
import { Album, AlbumApi, Media, SmartAlbum, Tag } from '../../../core/services/album-api';
import {
  AdvancedSearchValue,
  EMPTY_ADVANCED_SEARCH,
  StructuredSearchFilter,
  activeAdvancedSearchFilters,
  advancedSearchFromParams,
  advancedSearchToParams,
  createAdvancedSearchForm,
} from '../../../core/utils/advanced-search';
import { pagedList } from '../../../core/utils/paged-list';
import { Toast } from '../../../core/services/toast';

@Component({
  selector: 'app-albums',
  imports: [AlbumCard, FormsModule, ReactiveFormsModule, Icon, MediaGrid, Modal, ScrollReveal,
            SearchFilterFields, SmartAlbumCard, SmartAlbumEditor],
  templateUrl: './albums.html',
  styleUrl: './albums.css',
})
export class Albums {
  private readonly api = inject(AlbumApi);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly toast = inject(Toast);
  private readonly searchValue = signal<AdvancedSearchValue>({ ...EMPTY_ADVANCED_SEARCH });

  /** P01: las dos listas se cargan por paginas y APILAN. Antes pedian
   *  per_page 100 y 60 una sola vez, asi que el album 101 y el resultado 61
   *  no habia forma de alcanzarlos desde la interfaz. */
  private readonly albumPages = pagedList<Album>((page, perPage) => {
    const params: Record<string, string> = {
      role: 'owner', page: String(page), per_page: String(perPage),
    };
    if (this.searchValue().q) params['q'] = this.searchValue().q;
    return this.api.albums(params);
  });

  private readonly mediaPages = pagedList<Media>((page, perPage) => {
    const params = advancedSearchToParams(this.searchValue()) as Record<string, string>;
    return this.api.searchMedia({ ...params, page: String(page), per_page: String(perPage) });
  });

  private readonly filterAlbumPages = pagedList<Album>((page, perPage) =>
    this.api.albums({ role: 'owner', page: String(page), per_page: String(perPage) }), 100);

  private readonly filterTagPages = pagedList<Tag>((page, perPage) =>
    this.api.tags('', undefined, { page, perPage }), 100);

  /** L11: los álbumes inteligentes son otro tipo y otra lista; nunca entran
   *  en `albums`, que alimenta selectores de subida y de pertenencia. */
  private readonly smartPages = pagedList<SmartAlbum>((page, perPage) =>
    this.api.smartAlbums({ page: String(page), per_page: String(perPage) }));
  readonly smartAlbums = this.smartPages.items;
  readonly totalSmart = this.smartPages.total;
  readonly loadingSmart = this.smartPages.loading;
  hasMoreSmart() { return this.smartPages.hasMore(); }
  loadMoreSmart() { this.smartPages.loadMore(); }
  readonly smartEditorOpen = signal(false);
  /** Con qué criterios arranca el editor: vacío, o la búsqueda que había abierta. */
  readonly smartPrefill = signal<AdvancedSearchValue | null>(null);

  readonly totalAlbums = this.albumPages.total;
  readonly totalMedia = this.mediaPages.total;
  readonly loadingAlbums = this.albumPages.loading;
  readonly loadingMedia = this.mediaPages.loading;
  hasMoreAlbums() { return this.albumPages.hasMore(); }
  hasMoreMedia() { return this.mediaPages.hasMore(); }
  loadMoreAlbums() { this.albumPages.loadMore(); }
  loadMoreMedia() { this.mediaPages.loadMore(); }

  /** Solo se llena al buscar: sin término, esta página es la lista de álbumes. */
  readonly media = this.mediaPages.items;
  readonly albums = this.albumPages.items;
  readonly filterAlbums = this.filterAlbumPages.items;
  readonly tags = this.filterTagPages.items;
  creating = signal(false);
  saving = signal(false);
  /** El estado vacío espera a la primera respuesta para no parpadear. */
  readonly filtersOpen = signal(false);
  readonly activeFilters = computed(() => activeAdvancedSearchFilters(this.searchValue()));
  readonly hasStructuredFilters = computed(() => this.activeFilters().length > 0);
  readonly hasSearch = computed(() => Boolean(this.searchValue().q) || this.hasStructuredFilters());
  readonly backParams = computed(() => advancedSearchToParams(this.searchValue()) as Record<string, string>);
  readonly loaded = computed(() => this.hasSearch() ? this.mediaPages.loaded() : this.albumPages.loaded());
  readonly error = computed(() => this.mediaPages.error() || this.albumPages.error());

  readonly searchForm = createAdvancedSearchForm();

  query = '';
  title = '';
  description = '';
  isPrivate = true;

  closeCreate = () => this.creating.set(false);

  ngOnInit() {
    this.filterAlbumPages.loadAll();
    this.filterTagPages.loadAll();
    this.route.queryParamMap.subscribe(params => {
      const value = advancedSearchFromParams(params);
      this.searchForm.setValue(value, { emitEvent: false });
      this.searchValue.set(value);
      this.query = value.q;
      this.load();
    });
  }

  load() {
    this.albumPages.reset();

    // La barra de arriba es la única búsqueda siempre visible, así que busca
    // en las dos cosas: los álbumes y las fotos de dentro (título,
    // descripción y el texto que se haya detectado en ellas).
    if (!this.hasSearch()) {
      this.mediaPages.clear();
      this.mediaPages.total.set(0);
      this.smartPages.reset();
      return;
    }
    this.mediaPages.reset();
  }

  applySearch() {
    const value = this.searchForm.getRawValue();
    if (value.dateFrom && value.dateTo && value.dateFrom > value.dateTo) {
      this.toast.error('La fecha desde no puede ser posterior a la fecha hasta.');
      return;
    }
    this.router.navigate(['/albumes'], { queryParams: advancedSearchToParams(value) });
  }

  clearSearch() {
    this.searchForm.setValue({ ...EMPTY_ADVANCED_SEARCH });
    this.router.navigate(['/albumes']);
  }

  clearQuery() {
    this.searchForm.controls.q.setValue('');
    this.applySearch();
  }

  removeFilter(filter: StructuredSearchFilter) {
    switch (filter) {
      case 'mediaType': this.searchForm.controls.mediaType.setValue(''); break;
      case 'dateFrom': this.searchForm.controls.dateFrom.setValue(''); break;
      case 'dateTo': this.searchForm.controls.dateTo.setValue(''); break;
      case 'year': this.searchForm.controls.year.setValue(''); break;
      case 'albumId': this.searchForm.controls.albumId.setValue(''); break;
      case 'favorite': this.searchForm.controls.favorite.setValue(''); break;
      case 'tagId': this.searchForm.controls.tagId.setValue(''); break;
      case 'place': this.searchForm.controls.place.setValue(''); break;
      case 'archived': this.searchForm.controls.archived.setValue(''); break;
    }
    this.applySearch();
  }

  filterLabel(filter: StructuredSearchFilter) {
    const value = this.searchValue();
    switch (filter) {
      case 'mediaType': return value.mediaType === 'image' ? 'Fotos' : 'Videos';
      case 'dateFrom': return `Desde ${value.dateFrom}`;
      case 'dateTo': return `Hasta ${value.dateTo}`;
      case 'year': return `Año ${value.year}`;
      case 'albumId': return `Álbum: ${this.filterAlbums().find(a => String(a.id) === value.albumId)?.titulo || value.albumId}`;
      case 'favorite': return value.favorite === 'true' ? 'Favoritos' : 'No favoritos';
      case 'tagId': return `Tag: ${this.tags().find(tag => String(tag.id) === value.tagId)?.name || value.tagId}`;
      case 'place': return `Lugar: ${value.place}`;
      case 'archived': return value.archived === 'only' ? 'Solo archivados' : 'Sin archivados';
    }
  }

  openSmartCreate() {
    this.smartPrefill.set(null);
    this.smartEditorOpen.set(true);
  }

  /** Guarda como álbum inteligente exactamente lo que se está buscando. */
  saveSearchAsSmart() {
    this.smartPrefill.set({ ...this.searchValue() });
    this.smartEditorOpen.set(true);
  }

  onSmartSaved(smart: SmartAlbum) {
    this.smartEditorOpen.set(false);
    this.router.navigate(['/albumes/inteligentes', smart.id]);
  }

  openCreate() {
    this.title = '';
    this.description = '';
    this.isPrivate = true;
    this.creating.set(true);
  }

  create() {
    const titulo = this.title.trim();
    if (!titulo || this.saving()) return;

    this.saving.set(true);
    this.api.createAlbum({
      titulo,
      descripcion: this.description.trim() || null,
      is_private: this.isPrivate,
    }).subscribe({
      next: () => {
        this.saving.set(false);
        this.creating.set(false);
        this.toast.success('Álbum creado.');
        this.load();
      },
      error: error => {
        this.saving.set(false);
        this.toast.error(error?.error?.message || 'No se pudo crear el álbum.');
      },
    });
  }
}
