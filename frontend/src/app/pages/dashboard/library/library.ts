import { Component, computed, effect, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { MediaGrid } from '../../../components/ui/media-grid/media-grid';
import { Icon } from '../../../components/ui/icon/icon';
import { InfiniteScroll } from '../../../core/directives/infinite-scroll';
import { AlbumApi, LibraryMedia } from '../../../core/services/album-api';
import { cursorList, groupLibraryMedia } from '../../../core/utils/library-timeline';

@Component({
  selector: 'app-library',
  imports: [MediaGrid, Icon, InfiniteScroll, RouterLink],
  templateUrl: './library.html',
  styleUrl: './library.css',
})
export class Library {
  private readonly api = inject(AlbumApi);
  private readonly route = inject(ActivatedRoute);
  private focusHandled = false;
  /** `/archivo` es esta misma pagina con solo lo archivado (P09). */
  readonly archived = this.route.snapshot.data?.['archived'] === true;

  readonly timeline = cursorList<LibraryMedia>(
    (cursor, limit) => this.api.library(cursor, limit, this.archived),
    60,
    item => item.id,
  );
  readonly groups = computed(() => groupLibraryMedia(this.timeline.items()));

  /** F02: «Añadidos recientemente» vive aquí (antes en Inicio). Solo en
   * Biblioteca, no en Archivo; si está vacío la biblioteca también lo está y
   * el estado vacío de la cronología ya lo explica, así que no se pinta. */
  readonly recent = signal<LibraryMedia[]>([]);
  readonly recentState = signal<'loading' | 'ready' | 'error'>('loading');
  readonly initialLoading = computed(() => !this.timeline.loaded() && this.timeline.loading());
  readonly automaticLoad = computed(() =>
    this.timeline.hasMore() && !this.timeline.loading() && !this.timeline.error()
  );
  readonly focusId = (() => {
    const value = Number(this.route.snapshot.queryParamMap.get('focus'));
    return Number.isInteger(value) && value > 0 ? value : null;
  })();

  constructor() {
    effect(() => {
      if (this.focusHandled || !this.focusId || !this.timeline.loaded()) return;
      const found = this.timeline.items().some(item => item.id === this.focusId);
      if (found) {
        this.focusHandled = true;
        setTimeout(() => {
          const element = document.getElementById(`media-${this.focusId}`);
          element?.scrollIntoView?.({ block: 'center' });
          element?.focus({ preventScroll: true });
        });
        return;
      }
      if (!this.timeline.loading() && !this.timeline.error() && this.timeline.hasMore()) {
        this.timeline.loadMore();
      } else if (!this.timeline.loading() && !this.timeline.hasMore()) {
        this.focusHandled = true;
      }
    });
  }

  ngOnInit() {
    this.timeline.reset();
    if (!this.archived) this.loadRecent();
  }

  loadRecent() {
    this.recentState.set('loading');
    this.api.libraryRecent().subscribe({
      next: response => { this.recent.set(response.data); this.recentState.set('ready'); },
      error: () => this.recentState.set('error'),
    });
  }

  retry() {
    if (this.timeline.items().length) this.timeline.loadMore();
    else this.timeline.reset();
  }
}
