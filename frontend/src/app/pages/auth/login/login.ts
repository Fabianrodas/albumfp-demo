import { Component, computed, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { finalize } from 'rxjs';
import { Auth } from '../../../core/services/auth';
import { Icon } from '../../../components/ui/icon/icon';
import { friendlyAuthError, requiredError } from '../../../core/utils/auth-validation';
import { passkeyCancelled, passkeysSupported } from '../../../core/utils/webauthn';
import { sessionHint } from '../../../core/services/session-hint';

@Component({ selector: 'app-login', imports: [RouterLink, FormsModule, Icon], templateUrl: './login.html', styleUrl: './login.css' })
export class Login {
  /* La app es zoneless: lo que cambia fuera de un evento de plantilla solo
     repinta si es un signal. Con campos planos el error del backend se
     guardaba y no llegaba a verse nunca. */
  readonly username = signal('');
  readonly password = signal('');
  readonly showPassword = signal(false);
  readonly error = signal('');
  readonly loading = signal(false);
  /** Arranca marcada si la última sesión se guardó así: quien ya eligió
   * recordar este dispositivo no tiene por qué repetirlo cada vez. */
  readonly remember = signal(sessionHint.remembered());
  /** L15: el botón solo aparece donde el navegador sabe hacer WebAuthn. */
  readonly passkeys = passkeysSupported();
  readonly passkeyLoading = signal(false);

  /* Un campo solo enseña su fallo cuando ya lo tocaste o cuando intentaste
     enviar: nadie quiere leer "campo obligatorio" antes de escribir. */
  private readonly touched = signal<ReadonlySet<string>>(new Set());
  private readonly submitted = signal(false);

  readonly usernameMsg = computed(() => this.visible('username', requiredError(this.username(), 'Escribe tu usuario.')));
  readonly passwordMsg = computed(() => this.visible('password', this.password() ? '' : 'Escribe tu contraseña.'));

  constructor(private auth: Auth, private router: Router, private route: ActivatedRoute) {}

  touch(field: string) {
    this.touched.update(fields => new Set(fields).add(field));
  }

  private visible(field: string, message: string) {
    return this.submitted() || this.touched().has(field) ? message : '';
  }

  submit() {
    if (this.loading()) return;
    this.submitted.set(true);
    const username = this.username().trim();
    if (!username || !this.password()) return;

    this.error.set('');
    this.loading.set(true);
    this.auth.login({ username, password: this.password() }, this.remember()).pipe(
      finalize(() => this.loading.set(false))
    ).subscribe({
      next: () => this.continue(),
      error: e => this.error.set(friendlyAuthError(e, 'No fue posible iniciar sesión.')),
    });
  }

  async passkeyLogin() {
    if (this.passkeyLoading() || this.loading()) return;
    this.error.set('');
    this.passkeyLoading.set(true);
    try {
      await this.auth.loginWithPasskey(this.remember());
      this.continue();
    } catch (e) {
      this.error.set(passkeyCancelled(e)
        ? 'No se usó ninguna passkey. Puedes intentarlo otra vez o entrar con tu contraseña.'
        : friendlyAuthError(e as HttpErrorResponse, 'No fue posible entrar con la passkey.'));
    } finally {
      this.passkeyLoading.set(false);
    }
  }

  private continue() {
    const requested = this.route.snapshot.queryParamMap.get('returnUrl') || '/inicio';
    const target = requested.startsWith('/') && !requested.startsWith('//') ? requested : '/inicio';
    this.router.navigateByUrl(target);
  }
}
