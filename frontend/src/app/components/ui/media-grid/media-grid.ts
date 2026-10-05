import { Component, inject, input, output } from '@angular/core';
import { Router } from '@angular/router';
import { AlbumCapability, AlbumRole, canDeleteMedia, canEditAlbum, canOrganizeMedia } from '../../../core/models/album-permissions';
import { Media } from '../../../core/services/album-api';
import { mediaDetailLink } from '../../../core/utils/media-navigation';
import { Icon } from '../icon/icon';
import { MediaAsset } from '../media-asset/media-asset';

@Component({
  selector: 'app-media-grid',
  imports: [Icon, MediaAsset],
  templateUrl: './media-grid.html',
  styleUrl: './media-grid.css',
})
export class MediaGrid {
  private readonly router = inject(Router);

  items = input.required<Media[]>();
  mode = input<'favorites' | 'shared' | 'trash' | 'album' | 'places' | 'search' | 'library' | 'home' | 'archive' | 'smart'>('album');
  /** Se anexa al enlace del detalle para que "volver" reconstruya la vista de
   * origen y no solo su ruta — la búsqueda pierde su término sin esto. */
  backParams = input<Record<string, string>>({});
  role = input<AlbumRole>('read');
  capabilities = input<AlbumCapability[]>([]);
  shareToken = input<string | null>(null);

  /** Muestra la casilla de selección en cada tarjeta. La página dueña de la vista mantiene la selección. */
  selectable = input(false);
  selected = input<ReadonlySet<number>>(new Set<number>());

  restore = output<Media>();
  permanentDelete = output<Media>();
  deleteRequested = output<Media>();
  favoriteChanged = output<Media>();
  coverRequested = output<Media>();
  selectionToggled = output<Media>();
  /** Con shareToken, abrir una foto no puede navegar a la ruta autenticada
   * (rebotaría a /login) -- el padre decide qué hacer (p. ej. un lightbox). */
  itemOpened = output<Media>();

  canDelete() { return canDeleteMedia(this.role(), this.capabilities()); }
  canOrganize() { return canOrganizeMedia(this.role(), this.capabilities()); }
  /** Poner de portada escribe en el álbum, no en la foto. */
  canSetCover() { return canEditAlbum(this.role(), this.capabilities()); }
  isSelected(item: Media) { return this.selected().has(item.id); }

  /** Con una selección abierta, tocar una tarjeta la marca en vez de abrirla. */
  activate(item: Media) {
    if (this.selectable() && this.selected().size > 0) {
      this.selectionToggled.emit(item);
      return;
    }
    this.openDetail(item);
  }

  /** El álbum de contexto con el que abrir el detalle. Solo lo da el propio
   * álbum: en una vista global (biblioteca, inicio, favoritos, archivo,
   * lugares, búsqueda) el `album_id` del listado es presentación, no
   * identidad, así que el recuerdo se abre por sí mismo (`/recuerdos/:id`)
   * tenga cero, uno o varios álbumes. */
  private detailContextAlbum(item: Media) {
    return this.mode() === 'album' ? item.album_id : null;
  }

  openDetail(item: Media) {
    if (this.mode() === 'trash') return;
    if (this.shareToken()) { this.itemOpened.emit(item); return; }
    this.router.navigate(mediaDetailLink(this.detailContextAlbum(item), item.id), {
      // El detalle usa esto para ofrecer "Volver a favoritos" o "a compartido"
      // en lugar de mandar siempre a álbumes.
      queryParams: {
        from: this.mode(),
        ...this.backParams(),
        ...(this.mode() === 'library' || this.mode() === 'archive' ? { focus: item.id } : {}),
      },
    });
  }

  retentionLabel(item: Media) {
    if (!item.purge_at) return 'Hasta 30 días';
    const remaining = Math.max(0, Math.ceil((new Date(item.purge_at).getTime() - Date.now()) / 86400000));
    return remaining === 1 ? '1 día restante' : `${remaining} días restantes`;
  }
}
