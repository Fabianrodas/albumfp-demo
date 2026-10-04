import { Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { SmartAlbum } from '../../../core/services/album-api';
import { smartFilterSummary } from '../../../core/utils/advanced-search';
import { Icon } from '../icon/icon';

/** Tarjeta de un álbum inteligente. No reutiliza `album-card` a propósito: no
 * tiene portada, rol ni privacidad, porque no es un álbum que contenga nada. */
@Component({
  selector: 'app-smart-album-card',
  imports: [RouterLink, Icon],
  templateUrl: './smart-album-card.html',
  styleUrl: './smart-album-card.css',
})
export class SmartAlbumCard {
  smart = input.required<SmartAlbum>();
  readonly summary = computed(() => smartFilterSummary(this.smart().filters, this.smart().references));
  readonly broken = computed(() => this.smart().stale_references.length > 0);
}
