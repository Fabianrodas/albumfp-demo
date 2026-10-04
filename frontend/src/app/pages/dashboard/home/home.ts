import { Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { ExploreFeed } from '../../../components/discovery/explore-feed/explore-feed';
import { PeopleDirectory } from '../../../components/discovery/people-directory/people-directory';
import { Icon } from '../../../components/ui/icon/icon';
import { MediaGrid } from '../../../components/ui/media-grid/media-grid';
import { ScrollReveal } from '../../../core/directives/scroll-reveal';
import { AlbumApi, HomeMemory, PersonalHome } from '../../../core/services/album-api';
import { Auth } from '../../../core/services/auth';

export function localCalendarDate(value = new Date()): string {
  const pad = (part: number) => String(part).padStart(2, '0');
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
}
@Component({
  selector: 'app-home',
  imports: [RouterLink, Icon, MediaGrid, ScrollReveal, ExploreFeed, PeopleDirectory],
  templateUrl: './home.html',
  styleUrl: './home.css',
})
export class Home {
  private readonly api = inject(AlbumApi);
  private readonly auth = inject(Auth);
  private readonly route = inject(ActivatedRoute);

  /** Personas es secundario: no se pide nada hasta que se abre. La URL vieja
   * `/usuarios` llega como `/inicio?panel=personas` y lo abre. */
  readonly peopleOpen = signal(this.route.snapshot.queryParamMap.get('panel') === 'personas');

  readonly data = signal<PersonalHome | null>(null);
  readonly loading = signal(true);
  readonly error = signal('');
  readonly displayName = computed(() => this.auth.user()?.full_name || this.auth.user()?.username || 'hola');
  readonly greeting = computed(() => {
    const hour = new Date().getHours();
    if (hour < 12) return 'Buenos días';
    if (hour < 19) return 'Buenas tardes';
    return 'Buenas noches';
  });
  readonly memoryGroups = computed(() => {
    const groups = new Map<number, { year: number; yearsAgo: number; total: number; items: HomeMemory[] }>();
    for (const item of this.data()?.on_this_day.items ?? []) {
      const group = groups.get(item.memory_year) ?? {
        year: item.memory_year,
        yearsAgo: item.years_ago,
        total: item.year_total,
        items: [],
      };
      group.items.push(item);
      groups.set(item.memory_year, group);
    }
    return [...groups.values()];
  });

  ngOnInit() {
    this.load();
    if (this.peopleOpen()) setTimeout(() => document.getElementById('people-title')?.scrollIntoView?.({ block: 'start' }));
  }

  togglePeople() { this.peopleOpen.update(open => !open); }

  load() {
    this.loading.set(true);
    this.error.set('');
    this.api.home(localCalendarDate()).subscribe({
      next: response => {
        this.data.set(response.data);
        this.loading.set(false);
      },
      error: error => {
        this.error.set(error?.error?.message || 'No se pudo cargar tu inicio.');
        this.loading.set(false);
      },
    });
  }
}
