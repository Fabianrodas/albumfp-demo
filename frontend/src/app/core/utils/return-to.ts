/**
 * «Regresar al post» (v1.1): el autor de un comentario enlaza a su perfil con
 * `{ back, backLabel }` como estado de navegación. Aquí se valida antes de
 * usarlo: solo rutas internas de esta app, nunca `//host` ni `/\host`, que el
 * navegador trataría como otro origen.
 */
export function readReturnTo(state: unknown = history.state): { back: string; label: string } | null {
  const s = state as { back?: unknown; backLabel?: unknown } | null;
  const back = s?.back;
  if (typeof back !== 'string' || !back.startsWith('/') || back.startsWith('//') || back.startsWith('/\\')) return null;
  return { back, label: typeof s?.backLabel === 'string' ? s.backLabel : 'Volver' };
}
