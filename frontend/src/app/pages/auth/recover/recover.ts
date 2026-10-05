import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { finalize } from 'rxjs';
import { Icon } from '../../../components/ui/icon/icon';
import { AccountApi } from '../../../core/services/account-api';
import { confirmPasswordError, friendlyAuthError, newPasswordError, requiredError } from '../../../core/utils/auth-validation';

/** 30 símbolos base32 (sin 0/1/8/9), en grupos de 5 como se muestran al generarlos. */
function normalizeCode(value: string): string {
  const raw = value.replace(/[\s-]+/g, '').toUpperCase();
  return /^[A-Z2-7]{30}$/.test(raw) ? raw.match(/.{5}/g)!.join('-') : '';
}

/**
 * Recuperar la cuenta sin email (L14): usuario + un código de recuperación +
 * contraseña nueva. Nada de lo escrito se guarda en el navegador, y al terminar
 * los campos se vacían. No inicia sesión: se entra después con la contraseña nueva.
 */
@Component({ selector: 'app-recover', imports: [FormsModule, RouterLink, Icon], templateUrl: './recover.html', styleUrl: '../register/register.css' })
export class Recover {
  private readonly account = inject(AccountApi);

  readonly username = signal('');
  readonly code = signal('');
  readonly password = signal('');
  readonly confirm = signal('');
  readonly showPassword = signal(false);
  readonly loading = signal(false);
  readonly error = signal('');
  readonly done = signal(false);
  private readonly submitted = signal(false);

  readonly usernameMsg = computed(() => this.submitted() ? requiredError(this.username(), 'Escribe tu usuario.') : '');
  readonly codeMsg = computed(() => this.submitted() && !normalizeCode(this.code())
    ? 'Un código tiene 30 caracteres (letras y números del 2 al 7), por ejemplo ABCDE-FGHIJ-…' : '');
  readonly passwordMsg = computed(() => this.submitted() ? newPasswordError(this.password()) : '');
  readonly confirmMsg = computed(() => this.submitted() ? confirmPasswordError(this.password(), this.confirm()) : '');

  submit() {
    if (this.loading()) return;
    this.submitted.set(true);
    const code = normalizeCode(this.code());
    if (this.usernameMsg() || !code || this.passwordMsg() || this.confirmMsg()) return;

    this.error.set('');
    this.loading.set(true);
    this.account.recover({ username: this.username().trim(), code, new_password: this.password() }).pipe(
      finalize(() => this.loading.set(false)),
    ).subscribe({
      next: () => {
        this.code.set('');
        this.password.set('');
        this.confirm.set('');
        this.done.set(true);
      },
      error: e => this.error.set(friendlyAuthError(e, 'No pudimos recuperar la cuenta con esos datos.')),
    });
  }
}
