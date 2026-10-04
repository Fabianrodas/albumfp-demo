import { of, Subject, throwError } from 'rxjs';
import { LibraryMedia } from '../services/album-api';
import { cursorList, groupLibraryMedia, LibraryCursorPage } from './library-timeline';

const item = (id: number, effectiveDate: string): LibraryMedia => ({
  id,
  album_id: 7,
  album_titulo: 'Viajes',
  file_type: 'image',
  created_at: effectiveDate,
  effective_date: effectiveDate,
});

const page = <T>(data: T[], nextCursor: string | null, total = data.length): LibraryCursorPage<T> => ({
  data,
  message: 'ok',
  meta: { next_cursor: nextCursor, total, limit: 2 },
});

describe('cursorList', () => {
  it('apila paginas, elimina ids repetidos y avanza con el cursor del servidor', () => {
    const seen: Array<string | null> = [];
    const list = cursorList<LibraryMedia>((cursor) => {
      seen.push(cursor);
      return cursor === null
        ? of(page([item(3, '2026-09-18T10:00:00'), item(2, '2026-09-17T10:00:00')], 'next', 3))
        : of(page([
            item(2, '2026-09-17T10:00:00'),
            item(1, '2026-09-16T10:00:00'),
            item(1, '2026-09-16T10:00:00'),
          ], null, 3));
    }, 2, media => media.id);

    list.reset();
    list.loadMore();

    expect(seen).toEqual([null, 'next']);
    expect(list.items().map(media => media.id)).toEqual([3, 2, 1]);
    expect(list.total()).toBe(3);
    expect(list.hasMore()).toBe(false);
  });

  it('conserva paginas visibles si una pagina posterior falla y permite reintentar', () => {
    let attempts = 0;
    const list = cursorList<number>((cursor) => {
      if (cursor === null) return of(page([3, 2], 'next', 3));
      attempts++;
      return attempts === 1
        ? throwError(() => ({ error: { message: 'La red se corto' } }))
        : of(page([1], null, 3));
    }, 2);

    list.reset();
    list.loadMore();
    expect(list.items()).toEqual([3, 2]);
    expect(list.error()).toBe('La red se corto');

    list.loadMore();
    expect(list.items()).toEqual([3, 2, 1]);
    expect(list.error()).toBe('');
  });

  it('ignora respuestas viejas despues de reset y corta una pagina vacia', () => {
    const oldPage = new Subject<LibraryCursorPage<number>>();
    let calls = 0;
    const list = cursorList<number>(() => {
      calls++;
      return calls === 1 ? oldPage : of(page([], 'cursor-incorrecto', 99));
    });

    list.reset();
    list.reset();
    oldPage.next(page([99], null, 1));

    expect(list.items()).toEqual([]);
    expect(list.hasMore()).toBe(false);
    list.loadMore();
    expect(calls).toBe(2);
  });
});

describe('groupLibraryMedia', () => {
  it('agrupa en el orden recibido por ano y mes sin reordenar empates', () => {
    const groups = groupLibraryMedia([
      item(5, '2026-09-18T10:00:00'),
      item(4, '2026-09-18T10:00:00'),
      item(3, '2026-08-01T10:00:00'),
      item(2, '2025-12-31T10:00:00'),
    ]);

    expect(groups.map(group => group.year)).toEqual([2026, 2025]);
    expect(groups[0].months.map(month => month.key)).toEqual(['2026-09', '2026-08']);
    expect(groups[0].months[0].items.map(media => media.id)).toEqual([5, 4]);
    expect(groups[0].months[0].label).toBe('septiembre');
  });
});
