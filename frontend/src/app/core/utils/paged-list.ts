import { signal } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiResponse } from '../services/album-api';

/**
 * Una lista que se carga por páginas y va APILANDO, no reemplazando.
 *
 * El backend ya paginaba bien todas las colecciones (`page`/`per_page` con un
 * total autoritativo y un tope de 100 en el servidor). Lo que no paginaba era
 * el frontend: cada vista pedía `per_page=100` una sola vez y hacía
 * `.set(response.data)`, así que **el elemento 101 no existía para el usuario**
 * — la foto 101 de un álbum, el álbum 101 de la biblioteca. Este helper es lo
 * que faltaba, en un solo sitio en vez de repetido en seis componentes.
 *
 * **Por qué sigue siendo por página y no por cursor.** El plan pedía cursores
 * para evitar `OFFSET` profundo. Se descartó a propósito: `OFFSET` empieza a
 * doler cuando hay que saltarse cientos de miles de filas, y esto es una
 * biblioteca personal — el propio README la describe como app reservada. Migrar
 * cada listado a cursores es tocar todo el SQL de lectura y su orden, con
 * riesgo real de regresión, a cambio de nada medible a esta escala. Si algún
 * día un álbum pasa de ~100.000 fotos y se nota al final de la lista, este es
 * el sitio por donde entra el cambio: la firma de `fetch` no cambiaría, solo lo
 * que se le pasa.
 */
export interface PagedList<T> {
  /** Todo lo cargado hasta ahora, en orden. */
  items: ReturnType<typeof signal<T[]>>;
  /** El total que dice el SERVIDOR, no lo que llevamos cargado. */
  total: ReturnType<typeof signal<number | null>>;
  /** Hay una petición en vuelo. */
  loading: ReturnType<typeof signal<boolean>>;
  /** Ya volvió la primera página (para distinguir "vacío" de "cargando"). */
  loaded: ReturnType<typeof signal<boolean>>;
  error: ReturnType<typeof signal<string>>;
  hasMore: () => boolean;
  /** Vuelve a la página 1 y descarta lo cargado. Para filtros y búsquedas. */
  reset: () => void;
  /** Invalida peticiones pendientes y deja la lista vacía, sin pedir datos. */
  clear: () => void;
  /** Pide la siguiente página y la añade al final. */
  loadMore: () => void;
  /**
   * Trae TODAS las páginas seguidas.
   *
   * Solo para un selector, donde no cabe un boton: el `<select>` de albumes del
   * panel de subida o estan todos o el album 101 no se puede elegir. Para una
   * lista visible se usa `loadMore`, que es lo que evita traerse la biblioteca
   * entera de golpe.
   */
  loadAll: () => void;
}

export function pagedList<T>(
  fetch: (page: number, perPage: number) => Observable<ApiResponse<T[]>>,
  perPage = 60,
): PagedList<T> {
  const items = signal<T[]>([]);
  const total = signal<number | null>(null);
  const loading = signal(false);
  const loaded = signal(false);
  const error = signal('');
  let page = 0;
  let epoch = 0;

  const hasMore = () => {
    const conocido = total();
    if (conocido === null) return false;
    return items().length < conocido;
  };

  const request = (siguiente: number, append: boolean, alTerminar?: () => void) => {
    // Sin esta guarda, un doble clic en "Cargar más" -- o un scroll infinito
    // nervioso -- pide dos veces la misma página y duplica elementos.
    if (loading()) return;
    loading.set(true);
    error.set('');
    const requestEpoch = epoch;
    fetch(siguiente, perPage).subscribe({
      next: response => {
        if (requestEpoch !== epoch) return;
        const llegada = response.data || [];
        items.set(append ? [...items(), ...llegada] : llegada);
        // Sin bloque de paginación se asume que eso es todo: un endpoint que no
        // pagina no debe provocar peticiones infinitas.
        total.set(response.meta?.pagination?.total ?? items().length);
        page = siguiente;
        loading.set(false);
        loaded.set(true);
        alTerminar?.();
      },
      error: err => {
        if (requestEpoch !== epoch) return;
        // Un fallo al pedir la página 3 no puede vaciar las dos que ya se ven.
        error.set(err?.error?.message || 'No se pudo cargar el contenido.');
        loading.set(false);
        loaded.set(true);
      },
    });
  };

  return {
    items, total, loading, loaded, error, hasMore,
    clear: () => {
      epoch++;
      page = 0;
      items.set([]);
      total.set(null);
      loading.set(false);
      loaded.set(false);
      error.set('');
    },
    reset: () => {
      epoch++;
      page = 0;
      total.set(null);
      // `loading` se limpia a mano: si un reset llega con una petición vieja en
      // vuelo (cambiar de filtro dos veces seguidas), quedaría bloqueado.
      loading.set(false);
      request(1, false);
    },
    loadMore: () => {
      if (!hasMore()) return;
      request(page + 1, true);
    },
    loadAll: () => {
      epoch++;
      const siguiente = () => {
        if (!hasMore()) return;
        const antes = items().length;
        request(page + 1, true, () => {
          // Si una pagina no aporta ni una fila, se para. Un total mal
          // calculado en el servidor daria si no una peticion infinita desde
          // el navegador, y el bucle no tendria forma de salir.
          if (items().length > antes) siguiente();
        });
      };
      page = 0;
      total.set(null);
      loading.set(false);
      request(1, false, siguiente);
    },
  };
}
