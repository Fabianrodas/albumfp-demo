import { Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { finalize, switchMap } from 'rxjs';
import { Auth } from '../../../core/services/auth';
import { AlbumApi } from '../../../core/services/album-api';
import { Icon } from '../../../components/ui/icon/icon';
import {
  confirmPasswordError, friendlyAuthError, fullNameError, newPasswordError, newUsernameError, MIN_PASSWORD,
} from '../../../core/utils/auth-validation';

@Component({ selector: 'app-register', imports: [RouterLink, FormsModule, Icon], templateUrl: './register.html', styleUrl: './register.css' })
export class Register {
  private readonly api = inject(AlbumApi);
  private readonly route = inject(ActivatedRoute);

  /* Signals por el mismo motivo que en el login: sin zone.js solo repinta lo
     que es signal, y las validaciones se derivan de estos valores. */
  readonly fullName = signal('');
  readonly username = signal('');
  readonly password = signal('');
  readonly confirmPassword = signal('');
  readonly inviteToken = signal('');
  readonly showPassword = signal(false);
  readonly error = signal('');
  readonly loading = signal(false);

  /** `null` mientras se resuelve: el formulario no decide nada hasta saber
   * el modo real, para no mostrar (ni un instante) un formulario que el
   * servidor va a rechazar. El backend sigue siendo la autoridad; esto solo
   * evita ese parpadeo. */
  readonly registrationMode = signal<'open' | 'invite_only' | 'closed' | null>(null);

  private readonly touched = signal<ReadonlySet<string>>(new Set());
  private readonly submitted = signal(false);

  readonly minPassword = MIN_PASSWORD;

  readonly fullNameMsg = computed(() => this.visible('fullName', fullNameError(this.fullName())));
  readonly usernameMsg = computed(() => this.visible('username', newUsernameError(this.username())));
  readonly passwordMsg = computed(() => this.visible('password', newPasswordError(this.password())));
  readonly confirmMsg = computed(() =>
    this.visible('confirmPassword', confirmPasswordError(this.password(), this.confirmPassword())));

  constructor(private auth: Auth, private router: Router) {
    // Un enlace de invitacion trae el token en la URL (?invite=...); se
    // precarga aqui, pero el campo del formulario sigue siendo editable por
    // si alguien lo recibe pegado a mano en vez de como enlace.
    const desdeUrl = this.route.snapshot.queryParamMap.get('invite');
    if (desdeUrl) this.inviteToken.set(desdeUrl);

    this.api.health().subscribe({
      next: r => this.registrationMode.set(r.data.registration_mode),
      // Si /api/health falla, mejor dejar intentar el registro normal que
      // bloquear a todo el mundo por un problema de red pasajero: el
      // servidor sigue siendo quien de verdad decide.
      error: () => this.registrationMode.set('open'),
    });
  }

  touch(field: string) {
    this.touched.update(fields => new Set(fields).add(field));
  }

  private visible(field: string, message: string) {
    return this.submitted() || this.touched().has(field) ? message : '';
  }

  submit() {
    if (this.loading()) return;
    this.submitted.set(true);

    const invalid = fullNameError(this.fullName())
      || newUsernameError(this.username())
      || newPasswordError(this.password())
      || confirmPasswordError(this.password(), this.confirmPassword());
    if (invalid) return;

    this.error.set('');
    this.loading.set(true);
    const payload = {
      username: this.username().trim(),
      full_name: this.fullName().trim(),
      password: this.password(),
      ...(this.registrationMode() === 'invite_only' ? { invite_token: this.inviteToken().trim() } : {}),
    };
    this.auth.register(payload).pipe(
      switchMap(() => this.auth.login({ username: payload.username, password: payload.password })),
      finalize(() => this.loading.set(false)),
    ).subscribe({
      next: () => this.router.navigateByUrl('/inicio'),
      error: e => this.error.set(friendlyAuthError(e, 'No fue posible crear la cuenta.')),
    });
  }
}
