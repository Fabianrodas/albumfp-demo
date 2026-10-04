import { Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { MediaGrid } from '../../../components/ui/media-grid/media-grid';
import { PlaceCard } from '../../../components/ui/place-card/place-card';
import { ScrollReveal } from '../../../core/directives/scroll-reveal';
import { AlbumApi, Media, Place } from '../../../core/services/album-api';
import { pagedList } from '../../../core/utils/paged-list';

/** Un pais con sus lugares dentro. La cuadricula plana no decia nada de la
 * geografia; agrupar por pais es la unica jerarquia que los datos ya traen. */
interface CountryGroup {
  key: string;
  name: string;
  count: number;
  places: Place[];
}

/** Los tres componentes de un grupo de /places, tal como viajan en la URL. */
interface SelectedPlace {
  country_code: string | null;
  country_name: string | null;
  locality: string | null;
  region: string | null;
}

@Component({
  selector: 'app-places',
  imports: [RouterLink, MediaGrid, PlaceCard, ScrollReveal],
  templateUrl: './places.html',
  styleUrl: './places.css',
})
export class Places {
  private readonly api = inject(AlbumApi);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  readonly places = signal<Place[]>([]);
  /** P01: por paginas y apilando. Antes se pedia per_page=100 una sola vez,
   *  asi que un lugar con mas de 100 recuerdos ocultaba el resto. El filtro
   *  del lugar seleccionado viaja en `placeFilter`, y cambiarlo reinicia. */
  private placeFilter: Record<string, string> = {};
  private readonly placePages = pagedList<Media>((page, perPage) =>
    this.api.placeMedia({ ...this.placeFilter, page: String(page), per_page: String(perPage) }));

  readonly placeMedia = this.placePages.items;
  readonly totalPlaceMedia = this.placePages.total;
  readonly loadingMore = this.placePages.loading;
  hasMore() { return this.placePages.hasMore(); }
  loadMore() { this.placePages.loadMore(); }
  readonly selected = signal<SelectedPlace | null>(null);
  readonly loaded = this.placePages.loaded;
  readonly error = signal('');

  readonly countries = computed(() => new Set(this.places().map(p => p.country_code).filter(Boolean)).size);
  readonly cities = computed(() => this.places().filter(p => p.locality).length);
  readonly totalMemories = computed(() => this.places().reduce((sum, p) => sum + p.media_count, 0));

  /**
   * "3 ciudades en 2 paises, 47 recuerdos ubicados."
   *
   * Los mismos numeros iban en tres tarjetas-contador iguales arriba de la
   * pagina. Con una sola ciudad decian "1 / 1 / 1" y se comian un tercio de la
   * pantalla sin informar de nada; en una frase dicen lo mismo y dejan el sitio
   * a las fotos.
   */
  readonly summary = computed(() => {
    const ciudades = this.cities();
    const paises = this.countries();
    const total = this.totalMemories();
    const donde: string[] = [];
    if (ciudades) donde.push(`${ciudades} ${ciudades === 1 ? 'ciudad' : 'ciudades'}`);
    if (paises) donde.push(`en ${paises} ${paises === 1 ? 'país' : 'países'}`);
    const recuerdos = `${total} ${total === 1 ? 'recuerdo ubicado' : 'recuerdos ubicados'}`;
    return donde.length ? `${donde.join(' ')}, ${recuerdos}.` : `${recuerdos}.`;
  });

  /** Mas recuerdos primero, fuera y dentro de cada grupo: lo mas visitado manda. */
  readonly byCountry = computed<CountryGroup[]>(() => {
    const grupos = new Map<string, CountryGroup>();
    for (const place of this.places()) {
      const key = place.country_code || place.country_name || '';
      const grupo = grupos.get(key) ?? { key, name: place.country_name || 'Sin país', count: 0, places: [] };
      grupo.count += place.media_count;
      grupo.places.push(place);
      grupos.set(key, grupo);
    }
    return [...grupos.values()]
      .map(grupo => ({ ...grupo, places: [...grupo.places].sort((a, b) => b.media_count - a.media_count) }))
      .sort((a, b) => b.count - a.count);
  });

  /** "12 recuerdos · Guayas, Ecuador": lo que queda por encima del titulo del
   * lugar abierto, sin repetirlo. Mismo criterio que PlaceCard.subtitle. */
  readonly selectedSubtitle = computed(() => {
    const s = this.selected();
    if (!s) return '';
    // El total lo dice el servidor, no lo que llevamos cargado: con 250
    // recuerdos y 60 cargados, `length` diria 60.
    const total = this.totalPlaceMedia() ?? this.placeMedia().length;
    const recuerdos = `${total} ${total === 1 ? 'recuerdo' : 'recuerdos'}`;
    const resto = s.locality ? [s.region, s.country_name] : s.region ? [s.country_name] : [];
    const donde = resto.filter(Boolean).join(', ');
    return donde ? `${recuerdos} · ${donde}` : recuerdos;
  });

  /** "Guayaquil" o, sin ciudad, "Guayas" o "Ecuador": el mismo criterio que PlaceCard. */
  readonly selectedLabel = computed(() => {
    const s = this.selected();
    return s ? s.locality || s.region || s.country_name || 'Sin nombre' : '';
  });

  ngOnInit() {
    // Solo query params: la ruta base (/lugares) no cambia entre la cuadrícula
    // y un lugar abierto, así que Angular no vuelve a crear el componente.
    this.route.queryParamMap.subscribe(params => {
      const country_code = params.get('country_code');
      const country_name = params.get('country_name');
      const locality = params.get('locality');
      const region = params.get('region');

      if (country_code || locality || region) {
        this.selected.set({ country_code, country_name, locality, region });
        this.loadPlaceMedia({ country_code, locality, region });
      } else {
        this.selected.set(null);
        this.loadPlaces();
      }
    });
  }

  open(place: Place) {
    this.router.navigate(['/lugares'], {
      queryParams: {
        country_code: place.country_code,
        country_name: place.country_name,
        locality: place.locality,
        region: place.region,
      },
    });
  }

  private loadPlaces() {
    this.error.set('');
    this.loaded.set(false);
    this.api.places().subscribe({
      next: response => { this.places.set(response.data); this.loaded.set(true); },
      error: error => {
        this.error.set(error?.error?.message || 'No se pudieron cargar los lugares.');
        this.loaded.set(true);
      },
    });
  }

  private loadPlaceMedia(filter: { country_code: string | null; locality: string | null; region: string | null }) {
    this.error.set('');
    const params: Record<string, string> = {};
    if (filter.country_code) params['country_code'] = filter.country_code;
    if (filter.locality) params['locality'] = filter.locality;
    if (filter.region) params['region'] = filter.region;
    this.placeFilter = params;
    this.placePages.reset();
  }

}
