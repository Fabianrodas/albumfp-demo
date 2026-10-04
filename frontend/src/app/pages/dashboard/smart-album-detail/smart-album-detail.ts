import { Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { tap } from 'rxjs';
import { Icon } from '../../../components/ui/icon/icon';
import { MediaGrid } from '../../../components/ui/media-grid/media-grid';
import { SmartAlbumEditor } from '../../../components/ui/smart-album-editor/smart-album-editor';
import { AlbumApi, Media, SmartAlbum } from '../../../core/services/album-api';
import { Confirm } from '../../../core/services/confirm';
import { Toast } from '../../../core/services/toast';
import { smartFilterSummary } from '../../../core/utils/advanced-search';
import { pagedList } from '../../../core/utils/paged-list';

/**
 * Un álbum inteligente abierto: su definición y los recuerdos que la cumplen
 * AHORA, calculados por el servidor en cada carga. No es un álbum: no hay
 * subir, compartir, privacidad, portada ni pertenencias, y cada resultado se
 * abre como el recuerdo en sí (`/recuerdos/:id`).
 */
@Component({
  selector: 'app-smart-album-detail',
  imports: [RouterLink, Icon, MediaGrid, SmartAlbumEditor],
  templateUrl: './smart-album-detail.html',
  styleUrl: './smart-album-detail.css',
})
export class SmartAlbumDetail {
  private readonly api = inject(AlbumApi);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly confirm = inject(Confirm);
  private readonly toast = inject(Toast);

  readonly id = Number(this.route.snapshot.paramMap.get('id'));
  /** Lo que el detalle de un recuerdo necesita para volver aquí. */
  readonly backParams = { smart_album_id: String(this.id) };
  readonly smart = signal<SmartAlbum | null>(null);
  readonly error = signal('');
  /** El servidor respondió 409: un filtro guardado ya no existe. No se muestra
   * ningún resultado de repuesto hasta que el dueño lo arregle. */
  readonly broken = signal(false);
  readonly editing = signal(false);

  private readonly mediaPages = pagedList<Media>((page, perPage) =>
    this.api.smartAlbumMedia(this.id, { page: String(page), per_page: String(perPage) }).pipe(
      tap({ error: error => { if (error?.status === 409) this.broken.set(true); } }),
    ));
  readonly media = this.mediaPages.items;
  readonly total = this.mediaPages.total;
  readonly loaded = this.mediaPages.loaded;
  readonly loadingMore = this.mediaPages.loading;
  readonly summary = computed(() => {
    const smart = this.smart();
    return smart ? smartFilterSummary(smart.filters, smart.references) : [];
  });
  hasMore() { return this.mediaPages.hasMore(); }
  loadMore() { this.mediaPages.loadMore(); }

  ngOnInit() {
    if (!Number.isInteger(this.id) || this.id <= 0) {
      this.error.set('La dirección de este álbum inteligente no es válida.');
      return;
    }
    this.api.smartAlbum(this.id).subscribe({
      next: response => this.smart.set(response.data),
      error: error => this.error.set(error?.error?.message || 'No se pudo abrir el álbum inteligente.'),
    });
    this.mediaPages.reset();
  }

  onSaved(updated: SmartAlbum) {
    this.smart.set(updated);
    this.editing.set(false);
    this.broken.set(false);
    this.mediaPages.reset();
  }

  async remove() {
    const confirmed = await this.confirm.ask({
      title: 'Eliminar álbum inteligente',
      message: 'Se borra solo este álbum inteligente y sus filtros. Tus fotos y videos no se borran ni cambian: siguen en tu biblioteca y en sus álbumes.',
      confirmLabel: 'Eliminar',
      danger: true,
    });
    if (!confirmed) return;
    this.api.deleteSmartAlbum(this.id).subscribe({
      next: () => {
        this.toast.success('Álbum inteligente eliminado.');
        this.router.navigateByUrl('/albumes');
      },
      error: error => this.toast.error(error?.error?.message || 'No se pudo eliminar el álbum inteligente.'),
    });
  }
}
