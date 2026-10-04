/**
 * Lo poco que el navegador puede saber de la sesión desde S01.
 *
 * La credencial vive en una cookie `HttpOnly` que este código **no puede
 * leer** — ese es justamente el punto: un XSS ya no puede robarla. Lo que sí
 * es legible es la cookie CSRF, que se pone y se quita a la vez que la de
 * sesión y **no autentica nada por sí sola** (sin la cookie de sesión no vale
 * para nada).
 *
 * Sirve para dos cosas, y ninguna es autorizar:
 * - copiar el token CSRF a la cabecera de cada mutación;
 * - saber si *probablemente* hay sesión, para no disparar `GET /auth/me` en
 *   la portada pública cuando el visitante no tiene cuenta.
 *
 * Que "probablemente" sea mentira no tiene consecuencias: quien decide es
 * siempre el backend.
 */
const CSRF_COOKIE = /(?:^|;\s*)(?:__Host-)?albumfp_csrf=([^;]*)/;

/** Preferencia de UI, no una credencial: solo recuerda si la casilla
 * "Recuérdame" estaba marcada la última vez. */
const REMEMBER = 'albumfp_remember';

export const sessionHint = {
  /** Token CSRF, o `null` si no hay sesión en este navegador. */
  csrf(): string | null {
    const match = document.cookie.match(CSRF_COOKIE);
    return match ? decodeURIComponent(match[1]) : null;
  },

  /** Pista, no garantía: la cookie puede existir con la sesión ya revocada. */
  maybeSignedIn(): boolean {
    return this.csrf() !== null;
  },

  remembered(): boolean {
    try {
      return localStorage.getItem(REMEMBER) === '1';
    } catch {
      return false;
    }
  },

  /** Olvida la pista (no la credencial: esa la revoca el servidor). Evita que
   * un 401 con la cookie CSRF todavia puesta reintente en bucle. */
  forget() {
    for (const nombre of ['albumfp_csrf', '__Host-albumfp_csrf']) {
      document.cookie = `${nombre}=; Max-Age=0; Path=/`;
    }
  },

  rememberPreference(remember: boolean) {
    try {
      if (remember) localStorage.setItem(REMEMBER, '1');
      else localStorage.removeItem(REMEMBER);
    } catch {
      // Sin almacenamiento la casilla simplemente arranca desmarcada.
    }
  },
};
