import { signal } from '@angular/core';
import { Observable } from 'rxjs';
import { LibraryMedia } from '../services/album-api';

export interface LibraryCursorPage<T> {
  data: T[];
  message: string;
  meta: { next_cursor: string | null; total: number; limit: number };
}

export interface CursorList<T> {
  items: ReturnType<typeof signal<T[]>>;
  total: ReturnType<typeof signal<number | null>>;
  loading: ReturnType<typeof signal<boolean>>;
  loaded: ReturnType<typeof signal<boolean>>;
  error: ReturnType<typeof signal<string>>;
  hasMore: () => boolean;
  reset: () => void;
  clear: () => void;
  loadMore: () => void;
}

/** Accumulates stable cursor pages without allowing stale or duplicate rows. */
export function cursorList<T>(
  fetch: (cursor: string | null, limit: number) => Observable<LibraryCursorPage<T>>,
  limit = 60,
  identity: (item: T) => unknown = item => item,
): CursorList<T> {
  const items = signal<T[]>([]);
  const total = signal<number | null>(null);
  const loading = signal(false);
  const loaded = signal(false);
  const error = signal('');
  let nextCursor: string | null = null;
  let epoch = 0;

  const hasMore = () => loaded() && nextCursor !== null;

  const request = (cursor: string | null, append: boolean) => {
    if (loading()) return;
    loading.set(true);
    error.set('');
    const requestEpoch = epoch;
    fetch(cursor, limit).subscribe({
      next: response => {
        if (requestEpoch !== epoch) return;
        const arrived = response.data || [];
        if (append) {
          const known = new Set(items().map(identity));
          items.set([...items(), ...arrived.filter(value => {
            const key = identity(value);
            if (known.has(key)) return false;
            known.add(key);
            return true;
          })]);
        } else {
          const known = new Set<unknown>();
          items.set(arrived.filter(value => {
            const key = identity(value);
            if (known.has(key)) return false;
            known.add(key);
            return true;
          }));
        }
        total.set(response.meta?.total ?? items().length);
        // An empty page must terminate even if a faulty server emits a cursor.
        nextCursor = arrived.length ? (response.meta?.next_cursor ?? null) : null;
        loading.set(false);
        loaded.set(true);
      },
      error: err => {
        if (requestEpoch !== epoch) return;
        error.set(err?.error?.message || 'No se pudo cargar la biblioteca.');
        loading.set(false);
        loaded.set(true);
      },
    });
  };

  return {
    items, total, loading, loaded, error, hasMore,
    clear: () => {
      epoch++;
      nextCursor = null;
      items.set([]);
      total.set(null);
      loading.set(false);
      loaded.set(false);
      error.set('');
    },
    reset: () => {
      epoch++;
      nextCursor = null;
      items.set([]);
      total.set(null);
      loading.set(false);
      loaded.set(false);
      request(null, false);
    },
    loadMore: () => {
      if (!hasMore()) return;
      request(nextCursor, true);
    },
  };
}

export interface LibraryMonthGroup {
  key: string;
  label: string;
  items: LibraryMedia[];
}

export interface LibraryYearGroup {
  year: number;
  months: LibraryMonthGroup[];
}

const monthLabel = new Intl.DateTimeFormat('es', { month: 'long', timeZone: 'UTC' });

/** Groups an already ordered server timeline without changing its item order. */
export function groupLibraryMedia(items: LibraryMedia[]): LibraryYearGroup[] {
  const years = new Map<number, Map<string, LibraryMonthGroup>>();
  for (const item of items) {
    const match = /^(\d{4})-(\d{2})/.exec(item.effective_date);
    if (!match) continue;
    const year = Number(match[1]);
    const month = Number(match[2]);
    if (month < 1 || month > 12) continue;
    const key = `${match[1]}-${match[2]}`;
    let months = years.get(year);
    if (!months) {
      months = new Map();
      years.set(year, months);
    }
    let group = months.get(key);
    if (!group) {
      group = {
        key,
        label: monthLabel.format(new Date(Date.UTC(year, month - 1, 1))),
        items: [],
      };
      months.set(key, group);
    }
    group.items.push(item);
  }
  return Array.from(years, ([year, months]) => ({ year, months: Array.from(months.values()) }));
}
