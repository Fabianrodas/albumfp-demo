import { Component, effect, inject, input, output, signal, untracked } from '@angular/core';
import { FormsModule, ReactiveFormsModule } from '@angular/forms';
import { Album, AlbumApi, SmartAlbum, Tag } from '../../../core/services/album-api';
import { Toast } from '../../../core/services/toast';
import {
  AdvancedSearchValue,
  EMPTY_ADVANCED_SEARCH,
  createAdvancedSearchForm,
  searchFromSmartFilters,
  smartFiltersFromSearch,
} from '../../../core/utils/advanced-search';
import { pagedList } from '../../../core/utils/paged-list';
import { Modal } from '../modal/modal';
import { SearchFilterFields } from '../search-filter-fields/search-filter-fields';

/**
 * Crea o edita un álbum inteligente con los MISMOS controles que la búsqueda
 * avanzada. Guarda nombre, descripción y criterios; nunca orden ni página.
 * Con `smart` edita; sin él crea, opcionalmente partiendo de `prefill` (la
 * búsqueda que el usuario tenía abierta).
 */
@Component({
  selector: 'app-smart-album-editor',
  imports: [FormsModule, ReactiveFormsModule, Modal, SearchFilterFields],
  templateUrl: './smart-album-editor.html',
  styleUrl: './smart-album-editor.css',
})
export class SmartAlbumEditor {
  private readonly api = inject(AlbumApi);
  private readonly toast = inject(Toast);

  open = input(false);
  smart = input<SmartAlbum | null>(null);
  prefill = input<AdvancedSearchValue | null>(null);
  saved = output<SmartAlbum>();
  closed = output<void>();

  readonly form = createAdvancedSearchForm();
  title = '';
  description = '';
  readonly error = signal('');
  readonly saving = signal(false);

  /** Solo álbumes normales del dueño: un álbum inteligente nunca es criterio. */
  private readonly albumPages = pagedList<Album>((page, perPage) =>
    this.api.albums({ role: 'owner', page: String(page), per_page: String(perPage) }), 100);
  private readonly tagPages = pagedList<Tag>((page, perPage) =>
    this.api.tags('', undefined, { page, perPage }), 100);
  readonly albums = this.albumPages.items;
  readonly tags = this.tagPages.items;

  readonly close = () => this.closed.emit();

  constructor() {
    effect(() => {
      if (!this.open()) return;
      untracked(() => this.reset());
    });
  }

  private reset() {
    const smart = this.smart();
    this.title = smart?.titulo ?? '';
    this.description = smart?.descripcion ?? '';
    this.form.setValue(smart ? searchFromSmartFilters(smart.filters) : { ...(this.prefill() ?? EMPTY_ADVANCED_SEARCH) });
    this.error.set('');
    if (!this.albumPages.loaded()) this.albumPages.loadAll();
    if (!this.tagPages.loaded()) this.tagPages.loadAll();
  }

  save() {
    if (this.saving()) return;
    const titulo = this.title.trim();
    const value = this.form.getRawValue();
    const filters = smartFiltersFromSearch(value);
    if (!titulo) return this.error.set('Ponle un nombre al álbum inteligente.');
    if (!Object.keys(filters).length) return this.error.set('Elige al menos un filtro.');
    if (value.dateFrom && value.dateTo && value.dateFrom > value.dateTo) {
      return this.error.set('La fecha desde no puede ser posterior a la fecha hasta.');
    }
    this.error.set('');
    this.saving.set(true);
    const body = { titulo, descripcion: this.description.trim() || null, filters };
    const smart = this.smart();
    const request = smart ? this.api.updateSmartAlbum(smart.id, body) : this.api.createSmartAlbum(body);
    request.subscribe({
      next: response => {
        this.saving.set(false);
        this.toast.success(smart ? 'Álbum inteligente actualizado.' : 'Álbum inteligente creado.');
        this.saved.emit(response.data);
      },
      error: error => {
        this.saving.set(false);
        this.error.set(error?.error?.message || 'No se pudo guardar el álbum inteligente.');
      },
    });
  }
}
