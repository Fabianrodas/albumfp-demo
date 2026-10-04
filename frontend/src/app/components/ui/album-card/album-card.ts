import { Component, input } from '@angular/core';
import { DatePipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { Album } from '../../../core/services/album-api';
import { lazyCoverUrl } from '../../../core/utils/lazy-cover';
import { Icon } from '../icon/icon';

@Component({ selector: 'app-album-card', imports: [RouterLink, DatePipe, Icon], templateUrl: './album-card.html', styleUrl: './album-card.css' })
export class AlbumCard {
  album = input.required<Album>();
  /** De donde se abre esta tarjeta, para que "volver" en el album regrese
   * aqui. `null` (Mis albumes) no manda query params: es el origen por
   * defecto y ya vuelve ahi sin ayuda. */
  origin = input<'shared' | 'home' | null>(null);
  readonly coverUrl = lazyCoverUrl(
    () => this.album().cover_media_id,
    () => this.album().cover_file_type !== 'video',
  );
}
