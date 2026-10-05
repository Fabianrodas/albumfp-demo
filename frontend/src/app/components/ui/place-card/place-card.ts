import { Component, computed, input, output } from '@angular/core';
import { Place } from '../../../core/services/album-api';
import { lazyCoverUrl } from '../../../core/utils/lazy-cover';
import { Icon } from '../icon/icon';

@Component({ selector: 'app-place-card', imports: [Icon], templateUrl: './place-card.html', styleUrl: './place-card.css' })
export class PlaceCard {
  place = input.required<Place>();
  opened = output<Place>();

  readonly coverUrl = lazyCoverUrl(
    () => this.place().cover_media_id,
  );

  /** "Guayaquil" o, sin ciudad, "Guayas" o "Ecuador": nunca se inventa un nombre. */
  readonly title = computed(() => {
    const p = this.place();
    return p.locality || p.region || p.country_name || 'Sin nombre';
  });

  /** Lo que queda por encima del título (región, país), sin repetirlo. */
  readonly subtitle = computed(() => {
    const p = this.place();
    if (p.locality) return [p.region, p.country_name].filter(Boolean).join(', ');
    if (p.region) return p.country_name || '';
    return '';
  });
}
