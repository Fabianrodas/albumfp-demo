import { Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { PublicMedia } from '../../../core/services/album-api';
import { lazyCoverUrl } from '../../../core/utils/lazy-cover';
import { mediaDetailLink } from '../../../core/utils/media-navigation';
import { Icon } from '../icon/icon';
import { UserChip } from '../user-chip/user-chip';

/** Tarjeta del feed de fotos públicas: la propia foto (no una portada), con
 * su autor debajo enlazado a su perfil. */
@Component({
  selector: 'app-public-media-card',
  imports: [RouterLink, UserChip, Icon],
  templateUrl: './public-media-card.html',
  styleUrl: './public-media-card.css',
})
export class PublicMediaCard {
  media = input.required<PublicMedia>();

  readonly url = lazyCoverUrl(
    () => this.media().id,
    () => this.media().file_type !== 'video',
  );

  detailLink() {
    return mediaDetailLink(this.media().album_id, this.media().id);
  }
}
