import { Injectable, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, catchError, finalize, firstValueFrom, map, of, shareReplay, tap } from 'rxjs';
import { AlbumApi, ApiResponse } from './album-api';
import { sessionHint } from './session-hint';
import { authenticationJSON, requestOptions } from '../utils/webauthn';

const API = '';

export interface User {
  id: number;
  username: string;
  full_name: string;
  has_avatar: boolean;
  active?: boolean;
  last_login?: string;
  created_at?: string;
}

export interface AccountStats {
  total_albums: number;
  total_shared: number;
  total_favorites: number;
  storage_used_bytes: number;
  /** `null` = sin cuota configurada (S03): no pintar ninguna barra ni cifra. */
  storage_quota_bytes: number | null;
}

export interface Session {
  id: number;
  current: boolean;
  user_agent_summary: string | null;
  remember_me: boolean;
  created_at: string;
  last_used_at: string;
  expires_at: string;
}

@Injectable({ providedIn:'root' })
export class Auth {
  private readonly http = inject(HttpClient);
  private readonly albumApi = inject(AlbumApi);
  readonly user = signal<User | null>(null);
  /**
   * Cambia al subir una foto nueva. Va como parámetro en la URL del avatar
   * para que el navegador no siga mostrando la anterior desde su caché.
   */
  readonly avatarVersion = signal(Date.now());
  private readonly verified = signal(false);
  private sessionCheck$?: Observable<boolean>;

  /* Sin constructor a propósito. Aquí se lanzaba `ensureSession()` durante la
     construcción del propio servicio, y el interceptor hace `inject(Auth)`:
     pedía la instancia que todavía se estaba creando, la petición moría y el
     guard mandaba al login — el viejo "recargo y me pide iniciar sesión otra
     vez". Los guards (`authGuard`, `guestGuard`) ya llaman a `ensureSession()`
     con el inyector listo. */

  /** `remember` decide cuánto vive la sesión EN EL SERVIDOR y si su cookie
   * sobrevive a cerrar el navegador; en los dos casos sobrevive a recargar.
   * La respuesta no trae ninguna credencial: llega en una cookie HttpOnly. */
  login(credentials:{ username:string; password:string }, remember = false) {
    sessionHint.rememberPreference(remember);
    return this.http.post<ApiResponse<{user:User}>>(`${API}/auth/login`, { ...credentials, remember })
      .pipe(tap(response => {
        this.user.set(response.data.user);
        this.verified.set(true);
      }));
  }

  /** L15. Login sin usuario: el navegador ofrece las passkeys que tenga para
   * este sitio y el servidor sabe de quién es por la credencial elegida. Si
   * la persona cancela, el `DOMException` sube tal cual (ver `passkeyCancelled`). */
  async loginWithPasskey(remember = false): Promise<User> {
    const opciones = await firstValueFrom(
      this.http.post<ApiResponse<Record<string, any>>>(`${API}/auth/passkeys/login/options`, {}));
    const credencial = await navigator.credentials.get(requestOptions(opciones.data)) as PublicKeyCredential | null;
    if (!credencial) throw new DOMException('Sin passkey', 'NotAllowedError');
    sessionHint.rememberPreference(remember);
    const respuesta = await firstValueFrom(this.http.post<ApiResponse<{ user: User }>>(
      `${API}/auth/passkeys/login/verify`, { credential: authenticationJSON(credencial), remember }));
    this.user.set(respuesta.data.user);
    this.verified.set(true);
    return respuesta.data.user;
  }

  /** `invite_token` solo importa cuando el servidor esta en modo
   * `invite_only` (S03); el backend lo ignora en cualquier otro modo. */
  register(data:{ username:string; full_name:string; password:string; invite_token?:string }) {
    return this.http.post<ApiResponse<User>>(`${API}/auth/register`, data);
  }

  me() { return this.http.get<ApiResponse<User>>(`${API}/auth/me`); }
  stats() { return this.http.get<ApiResponse<AccountStats>>(`${API}/auth/me/stats`); }

  updateMe(data: { username?: string; full_name?: string }) {
    return this.http.patch<ApiResponse<User>>(`${API}/auth/me`, data)
      .pipe(tap(response => this.user.set(response.data)));
  }

  changePassword(data: { current_password: string; new_password: string }) {
    return this.http.post<ApiResponse<unknown>>(`${API}/auth/me/password`, data);
  }

  uploadAvatar(file: File) {
    const form = new FormData();
    form.append('file', file, file.name);
    return this.http.post<ApiResponse<{ has_avatar: boolean }>>(`${API}/auth/me/avatar`, form)
      .pipe(tap(() => {
        this.user.update(user => user ? { ...user, has_avatar: true } : user);
        this.avatarVersion.set(Date.now());
      }));
  }

  /** El backend exige el nombre de usuario escrito como confirmación. */
  deleteMe(username: string) {
    return this.http.delete<ApiResponse<{ removed_files: number }>>(`${API}/auth/me`, {
      body: { username },
    });
  }

  /** URL de la foto de perfil propia, con la marca de versión para evitar la caché. */
  myAvatarUrl() {
    const id = this.user()?.id;
    return id ? `${API}/api/users/${id}/avatar?v=${this.avatarVersion()}` : '';
  }

  /** La unica forma de CONFIRMAR una sesion es preguntarselo al servidor: la
   * cookie es HttpOnly y este codigo no puede leerla.
   *
   * Pero sin ningun indicio no se pregunta, y eso NO es una optimizacion: el
   * guard de invitado corre en /login, un 401 manda al interceptor a navegar
   * a /login, y eso vuelve a disparar el guard. Preguntando siempre, un
   * visitante sin sesion entraba en un bucle infinito de /auth/me (medido:
   * 4697 peticiones antes de cortarlo). El modelo viejo no lo sufria porque
   * sin tokens en Web Storage ni siquiera hacia la peticion. */
  ensureSession(): Observable<boolean> {
    if (this.verified() && this.user()) return of(true);
    if (this.sessionCheck$) return this.sessionCheck$;
    if (!sessionHint.maybeSignedIn()) return of(false);

    this.sessionCheck$ = this.me().pipe(
      map(response => {
        if (!response?.data) return false;
        this.user.set(response.data);
        this.verified.set(true);
        return true;
      }),
      catchError(() => {
        this.clearLocalState();
        return of(false);
      }),
      finalize(() => { this.sessionCheck$ = undefined; }),
      shareReplay({ bufferSize: 1, refCount: false }),
    );
    return this.sessionCheck$;
  }

  /** Cerrar sesion es ahora una operacion del SERVIDOR: revoca la fila, asi
   * que la credencial deja de valer venga de donde venga. Antes solo se
   * borraba la copia del navegador y un JWT robado seguia sirviendo hasta
   * caducar. El estado local se limpia pase lo que pase con la peticion: si
   * el servidor no contesta, lo que no puede quedar es la sesion pintada. */
  logout() {
    return this.http.post<ApiResponse<unknown>>(`${API}/auth/logout`, {}).pipe(
      catchError(() => of(null)),
      tap(() => this.clearLocalState()),
    );
  }

  /** Todos los dispositivos, este incluido. */
  logoutEverywhere() {
    return this.http.post<ApiResponse<{ revoked: number }>>(`${API}/auth/logout-all`, {}).pipe(
      tap(() => this.clearLocalState()),
    );
  }

  /** Pantalla de "Seguridad" del perfil. */
  sessions() { return this.http.get<ApiResponse<Session[]>>(`${API}/auth/sessions`); }

  /** Cierra una sesión ajena a esta (otro dispositivo). Para cerrar ESTA,
   * usa `logout()`: el servidor ya limpia la cookie de este navegador. */
  revokeSession(id: number) {
    return this.http.delete<ApiResponse<unknown>>(`${API}/auth/sessions/${id}`);
  }

  /** "Cerrar sesión en los demás dispositivos": a diferencia de
   * `logoutEverywhere()`, esta NO te saca a ti. */
  revokeOtherSessions() {
    return this.http.post<ApiResponse<{ revoked: number }>>(`${API}/auth/sessions/revoke-others`, {});
  }

  get isAuthenticated() { return this.verified() && !!this.user(); }

  /** Borra lo que se pinta y la PISTA de sesion.
   *
   * Tirar la cookie CSRF es parte del arreglo del bucle: si el servidor ya
   * rechazo la sesion pero la pista sigue puesta, `ensureSession()` volveria
   * a preguntar y el 401 volveria a redirigir. La credencial de verdad la
   * borra el servidor; esto solo cierra la puerta desde este lado.
   *
   * Cerrar sesión no recarga la página, así que lo memorizado en `AlbumApi`
   * también se va aquí: si no, la siguiente cuenta que entre en esta pestaña
   * recibiría de memoria los listados de la anterior. */
  clearLocalState() {
    this.user.set(null);
    this.verified.set(false);
    this.sessionCheck$ = undefined;
    sessionHint.forget();
    this.albumApi.forgetSession();
  }
}
