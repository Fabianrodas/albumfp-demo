import { of, Subject, throwError } from 'rxjs';
import { pagedList } from './paged-list';

/** Respuesta de la API con su bloque de paginación, como la manda DE VERDAD el
 *  backend: `ok(..., pagination=...)` la anida en `meta`. Hasta P08 este fixture
 *  la ponía en el primer nivel, así que las pruebas pasaban mientras en la app
 *  real `hasMore()` era siempre falso y el elemento 61 no se podía alcanzar. */
const page = <T>(data: T[], pageNumber: number, perPage: number, total: number) => ({
  data,
  message: 'ok',
  meta: { pagination: { page: pageNumber, per_page: perPage, total, total_pages: Math.ceil(total / perPage) } },
});

const numbers = (from: number, count: number) => Array.from({ length: count }, (_, i) => from + i);

/** Una pagina como la devolveria el backend: la ULTIMA trae solo el resto, no
 *  otra pagina entera. Un fixture que siempre devuelve `per_page` filas miente
 *  y hace pasar (o fallar) pruebas por el motivo equivocado. */
const slice = (pageNumber: number, perPage: number, total: number) =>
  numbers((pageNumber - 1) * perPage, Math.max(0, Math.min(perPage, total - (pageNumber - 1) * perPage)));

describe('pagedList', () => {
  it('carga la primera página y sabe que quedan más', () => {
    const lista = pagedList<number>((p, per) => of(page(numbers((p - 1) * per, per), p, per, 250)), 100);
    lista.reset();

    expect(lista.items().length).toBe(100);
    expect(lista.total()).toBe(250);
    expect(lista.hasMore()).toBe(true);
    expect(lista.loaded()).toBe(true);
  });

  it('APENDE la página siguiente en vez de reemplazarla', () => {
    // Este es el bug que P01 arregla: las vistas pedían per_page=100 y hacían
    // `.set(response.data)`, así que la foto 101 no existía para el usuario.
    const lista = pagedList<number>((p, per) => of(page(numbers((p - 1) * per, per), p, per, 250)), 100);
    lista.reset();
    lista.loadMore();

    expect(lista.items().length).toBe(200);
    expect(lista.items()[0]).toBe(0);
    expect(lista.items()[199]).toBe(199);
  });

  it('deja de pedir cuando ya tiene el total', () => {
    const lista = pagedList<number>((p, per) => of(page(numbers((p - 1) * per, 50), p, per, 150)), 50);
    lista.reset();
    lista.loadMore();
    lista.loadMore();

    expect(lista.items().length).toBe(150);
    expect(lista.hasMore()).toBe(false);
  });

  it('no pide dos veces la misma página si se pulsa dos veces seguidas', () => {
    // Sin esta guarda, un doble clic o un scroll infinito nervioso duplica
    // elementos en la lista.
    let llamadas = 0;
    const lista = pagedList<number>((p, per) => {
      llamadas++;
      return of(page(numbers((p - 1) * per, per), p, per, 500));
    }, 100);
    lista.reset();
    llamadas = 0;

    lista.loading.set(true);
    lista.loadMore();

    expect(llamadas).toBe(0);
  });

  it('reset vuelve a empezar y descarta lo cargado', () => {
    // Task 4 del plan: al cambiar un filtro o la búsqueda, la lista no puede
    // quedarse con los resultados del filtro anterior pegados delante.
    const lista = pagedList<number>((p, per) => of(page(numbers((p - 1) * per, per), p, per, 250)), 100);
    lista.reset();
    lista.loadMore();
    expect(lista.items().length).toBe(200);

    lista.reset();
    expect(lista.items().length).toBe(100);
    expect(lista.items()[0]).toBe(0);
  });

  it('ignora una respuesta vieja que llega después del reset actual', () => {
    const vieja = new Subject<ReturnType<typeof page<number>>>();
    const actual = new Subject<ReturnType<typeof page<number>>>();
    let llamada = 0;
    const lista = pagedList<number>(() => ++llamada === 1 ? vieja : actual, 100);

    lista.reset();
    lista.reset();
    actual.next(page([20], 1, 100, 1));
    vieja.next(page([10], 1, 100, 1));

    expect(lista.items()).toEqual([20]);
    expect(lista.total()).toBe(1);
  });

  it('clear invalida una respuesta pendiente sin iniciar otra petición', () => {
    const pendiente = new Subject<ReturnType<typeof page<number>>>();
    let llamadas = 0;
    const lista = pagedList<number>(() => { llamadas++; return pendiente; }, 100);

    lista.reset();
    lista.clear();
    pendiente.next(page([10], 1, 100, 1));

    expect(llamadas).toBe(1);
    expect(lista.items()).toEqual([]);
    expect(lista.loading()).toBe(false);
    expect(lista.error()).toBe('');
  });

  it('usa el total del servidor, no lo que lleva cargado', () => {
    // Task 3: el contador de Lugares decía "1" cuando había 1 cargado.
    const lista = pagedList<number>((p, per) => of(page(numbers(0, 100), p, per, 4321)), 100);
    lista.reset();

    expect(lista.total()).toBe(4321);
    expect(lista.items().length).toBe(100);
  });

  it('un error deja la lista utilizable en vez de en blanco', () => {
    const lista = pagedList<number>((p, per) =>
      p === 1 ? of(page(numbers(0, 100), p, per, 250)) : throwError(() => ({ error: { message: 'cayó' } })), 100);
    lista.reset();
    lista.loadMore();

    expect(lista.items().length).toBe(100);
    expect(lista.error()).toBe('cayó');
    expect(lista.loading()).toBe(false);
  });

  it('loadAll trae TODAS las páginas, para un selector que no puede tener botón', () => {
    // El `<select>` de álbumes del panel de subida no puede llevar un "Cargar
    // más" dentro: o están todos, o el álbum 101 no se puede elegir.
    let llamadas = 0;
    const lista = pagedList<number>((p, per) => {
      llamadas++;
      return of(page(slice(p, per, 250), p, per, 250));
    }, 100);
    lista.loadAll();

    expect(lista.items().length).toBe(250);
    expect(lista.hasMore()).toBe(false);
    expect(llamadas).toBe(3);
  });

  it('loadAll se planta si el servidor deja de devolver filas', () => {
    // Sin esta guarda, un total mal calculado en el servidor daría una
    // petición infinita desde el navegador.
    let llamadas = 0;
    const lista = pagedList<number>((p, per) => {
      llamadas++;
      return of(page([], p, per, 9999));
    }, 100);
    lista.loadAll();

    expect(llamadas).toBeLessThan(5);
    expect(lista.items().length).toBe(0);
  });

  it('sin bloque de paginación asume que no hay más', () => {
    // Un endpoint que no pagine no debe provocar una petición infinita.
    const lista = pagedList<number>(() => of({ data: numbers(0, 3), message: 'ok' }), 100);
    lista.reset();

    expect(lista.items().length).toBe(3);
    expect(lista.hasMore()).toBe(false);
    expect(lista.total()).toBe(3);
  });
});
