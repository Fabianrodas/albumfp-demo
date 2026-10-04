/**
 * Reglas de los formularios de cuenta. Replican lo que valida el backend
 * (`app/domain/rules.py` y `app/security/password_policy.py`) y añaden encima lo
 * que solo tiene sentido al crear la cuenta.
 *
 * El cliente puede ser más estricto que el servidor, nunca al revés: el usuario
 * se limita a letras, números, punto, guion y guion bajo solo al registrarse, y
 * por eso el login únicamente comprueba que el campo no esté vacío — si no,
 * dejaría fuera a cuentas que el servidor sí acepta.
 */

export const MAX_USERNAME = 50;
export const MIN_USERNAME = 3;
export const MAX_FULL_NAME = 120;
// NIST SP 800-63B (S02): sin reglas de composición, un mínimo más alto en su
// lugar. Coincide con `MIN_PASSWORD_LENGTH` en `password_policy.py`.
export const MIN_PASSWORD = 15;
export const MAX_PASSWORD = 128;

const USERNAME_SHAPE = /^[a-zA-Z0-9._-]+$/;

export function fullNameError(value: string): string {
  const name = value.trim();
  if (!name) return 'Escribe tu nombre completo.';
  if (name.length < 2) return 'El nombre es demasiado corto.';
  if (name.length > MAX_FULL_NAME) return `El nombre no puede superar ${MAX_FULL_NAME} caracteres.`;
  return '';
}

export function newUsernameError(value: string): string {
  const username = value.trim();
  if (!username) return 'Elige un nombre de usuario.';
  if (username.length < MIN_USERNAME) return `Usa al menos ${MIN_USERNAME} caracteres.`;
  if (username.length > MAX_USERNAME) return `No puede superar ${MAX_USERNAME} caracteres.`;
  if (!USERNAME_SHAPE.test(username)) return 'Solo letras, números, punto, guion y guion bajo.';
  return '';
}

export function newPasswordError(value: string): string {
  if (!value) return 'Escribe una contraseña.';
  if (value.length < MIN_PASSWORD) return `Necesita al menos ${MIN_PASSWORD} caracteres.`;
  if (value.length > MAX_PASSWORD) return `No puede superar ${MAX_PASSWORD} caracteres.`;
  // Misma regla que el backend: una contraseña de solo dígitos se rechaza.
  if (/^\d+$/.test(value)) return 'No puede ser solo números.';
  return '';
}

export function confirmPasswordError(password: string, confirm: string): string {
  if (!confirm) return 'Repite la contraseña.';
  if (password !== confirm) return 'Las contraseñas no coinciden.';
  return '';
}

export function requiredError(value: string, message: string): string {
  return value.trim() ? '' : message;
}

/** Mensaje de error de login/registro, con el `Retry-After` de un 429
 * incorporado cuando el backend lo manda (S03). Compartido entre los dos
 * formularios: misma logica, un solo sitio. */
export function friendlyAuthError(
  e: { status: number; error?: { message?: string }; headers?: { get(name: string): string | null } },
  fallback: string,
): string {
  const base = e.error?.message ?? fallback;
  if (e.status !== 429) return base;
  const retryAfter = Number(e.headers?.get('Retry-After'));
  return Number.isFinite(retryAfter) && retryAfter > 0 ? `${base} (unos ${retryAfter}s)` : base;
}
