import { Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { PublicAlbum } from '../../../core/services/album-api';
import { lazyCoverUrl } from '../../../core/utils/lazy-cover';
import { Icon } from '../icon/icon';
import { UserChip } from '../user-chip/user-chip';

/** Tarjeta del feed: portada, autor y nombre del álbum. */
@Component({
  selector: 'app-public-album-card',
  imports: [RouterLink, UserChip, Icon],
  templateUrl: './public-album-card.html',
  styleUrl: './public-album-card.css',
})
export class PublicAlbumCard {
  album = input.required<PublicAlbum>();
  /** El perfil de una persona ya muestra quién es en la cabecera. */
  showOwner = input(true);
  /** Para que "volver" desde el album regrese a este perfil. Viaja aparte de
   * `album()`: `GET /api/users/<u>/albums` no manda `owner` por item (todo el
   * listado es ya de una sola persona), asi que el dato tiene que venir de la
   * pagina de perfil, que si lo conoce. */
  profileUsername = input<string | null>(null);

  readonly coverUrl = lazyCoverUrl(
    () => this.album().cover_media_id,
    () => this.album().cover_file_type !== 'video',
  );
  readonly backParams = computed(() => {
    const username = this.profileUsername();
    return username ? { from: 'profile', username } : null;
  });
}
