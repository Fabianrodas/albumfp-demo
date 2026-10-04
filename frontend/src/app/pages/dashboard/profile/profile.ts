import { DatePipe } from '@angular/common';
import { Component, OnDestroy, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { Icon } from '../../../components/ui/icon/icon';
import { Modal } from '../../../components/ui/modal/modal';
import { AccountApi, ExportOptions, ExportSummary, Passkey, RecoveryStatus } from '../../../core/services/account-api';
import { AlbumApi } from '../../../core/services/album-api';
import { AccountStats, Auth, Session } from '../../../core/services/auth';
import { Confirm } from '../../../core/services/confirm';
import { NotificationPreferences, NotificationsApi } from '../../../core/services/notifications';
import { Toast } from '../../../core/services/toast';
import { MIN_PASSWORD } from '../../../core/utils/auth-validation';
import { describeDevice } from '../../../core/utils/device-label';
import { creationOptions, passkeyCancelled, passkeysSupported, registrationJSON } from '../../../core/utils/webauthn';

@Component({
  selector: 'app-profile',
  imports: [DatePipe, FormsModule, Icon, Modal, RouterLink],
  templateUrl: './profile.html',
  styleUrl: './profile.css',
})
export class Profile implements OnDestroy {
  readonly auth = inject(Auth);
  private readonly api = inject(AlbumApi);
  private readonly confirm = inject(Confirm);
  private readonly toast = inject(Toast);
  private readonly router = inject(Router);
  private readonly notifications = inject(NotificationsApi);
  private readonly account = inject(AccountApi);

  /** L13: EXIF, ubicación y OCR solo viajan si la persona los marca. */
  readonly exportOptionList: { key: keyof ExportOptions; label: string; hint: string }[] = [
    { key: 'exif', label: 'EXIF de cámara', hint: 'Marca, modelo y fecha original de cada foto.' },
    { key: 'location', label: 'Ubicación', hint: 'Coordenadas GPS y el lugar resuelto (ciudad, país).' },
    { key: 'ocr', label: 'Texto detectado', hint: 'El texto que AlbumFP leyó dentro de tus fotos.' },
  ];
  exportOptions: ExportOptions = { exif: false, location: false, ocr: false };
  exportSummary = signal<ExportSummary | null>(null);
  exportChecking = signal(false);
  exportError = signal('');

  /** L14. Los códigos en claro viven SOLO en esta señal, mientras se
   * enseñan: ni Web Storage, ni la URL, ni un log. Se vacían al pulsar «Ya
   * los guardé» y al salir de la página. */
  recoveryStatus = signal<RecoveryStatus | null>(null);
  revealedCodes = signal<string[]>([]);
  askingRecoveryPassword = signal(false);
  generatingCodes = signal(false);
  recoveryError = signal('');
  recoveryPassword = '';

  /** L15. La lista solo trae nombre y fechas; la clave pública nunca llega. */
  readonly passkeysSupported = passkeysSupported();
  passkeys = signal<Passkey[]>([]);
  addingPasskey = signal(false);
  registeringPasskey = signal(false);
  passkeyError = signal('');
  passkeyPassword = '';
  passkeyNickname = '';
  renaming = signal<Passkey | null>(null);
  renameNickname = '';

  /** L12: las tres categorías de avisos IN-APP. No hay push ni permisos del
   * navegador: esto solo decide qué avisos nuevos llegan a la campana. */
  readonly prefOptions: { key: keyof NotificationPreferences; label: string; hint: string }[] = [
    { key: 'notify_album_invites', label: 'Invitaciones a álbumes', hint: 'Cuando alguien te invita a colaborar en un álbum.' },
    { key: 'notify_share_claimed', label: 'Invitaciones aceptadas', hint: 'Cuando alguien acepta una invitación tuya.' },
    { key: 'notify_shared_album_uploads', label: 'Nuevas fotos en álbumes compartidos', hint: 'Cuando se sube un recuerdo a un álbum que compartes.' },
  ];
  prefs = signal<NotificationPreferences | null>(null);
  savingPref = signal<keyof NotificationPreferences | null>(null);

  stats = signal<AccountStats | null>(null);
  editing = signal(false);
  changingPassword = signal(false);
  saving = signal(false);

  readonly minPassword = MIN_PASSWORD;
  readonly describeDevice = describeDevice;

  sessions = signal<Session[]>([]);
  revokingSessionId = signal<number | null>(null);
  revokingOthers = signal(false);
  readonly hasOtherSessions = computed(() => this.sessions().some(s => !s.current));

  editFullName = '';
  editUsername = '';
  pickedAvatar: File | null = null;
  avatarPreview = signal('');

  currentPassword = '';
  newPassword = '';
  repeatPassword = '';

  readonly initials = computed(() => (this.auth.user()?.username || 'AlbumFP').slice(0, 2).toUpperCase());

  /** `null` = sin cuota configurada (S03): la fila entera desaparece, mismo
   * criterio que "sin GPS" o "sin festivo" en el resto de la app -- una
   * ausencia silenciosa en vez de un aviso que nadie puede resolver. */
  readonly storagePercent = computed(() => {
    const s = this.stats();
    if (!s?.storage_quota_bytes) return null;
    return Math.min(100, Math.round((s.storage_used_bytes / s.storage_quota_bytes) * 100));
  });

  formatBytes(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    const unidades = ['KB', 'MB', 'GB', 'TB'];
    let valor = bytes / 1024;
    let i = 0;
    while (valor >= 1024 && i < unidades.length - 1) { valor /= 1024; i++; }
    return `${valor.toFixed(valor >= 10 ? 0 : 1)} ${unidades[i]}`;
  }

  /** "agosto de 2026", en minuscula porque va dentro de una frase. Vacio si no
   * se sabe: la linea entera desaparece en vez de enseñar un hueco. */
  readonly memberSince = computed(() => {
    const raw = this.auth.user()?.created_at;
    if (!raw) return '';
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat('es', { month: 'long', year: 'numeric' }).format(date);
  });

  closeEdit = () => this.editing.set(false);
  closeRecoveryPassword = () => { this.askingRecoveryPassword.set(false); this.recoveryPassword = ''; };

  ngOnDestroy() { this.revealedCodes.set([]); }

  loadPasskeys() {
    this.account.passkeys().subscribe({ next: r => this.passkeys.set(r.data), error: () => {} });
  }

  openAddPasskey() {
    this.passkeyError.set('');
    this.passkeyPassword = '';
    this.passkeyNickname = '';
    this.addingPasskey.set(true);
  }

  closeAddPasskey = () => { this.addingPasskey.set(false); this.passkeyPassword = ''; };

  /** Contraseña → opciones del servidor → el diálogo del sistema crea la
   * credencial → el servidor la verifica. La contraseña no se guarda. */
  async confirmAddPasskey() {
    const nickname = this.passkeyNickname.trim();
    if (!nickname) { this.passkeyError.set('Ponle un nombre para reconocerla (por ejemplo, «Portátil»).'); return; }
    const password = this.passkeyPassword;
    this.passkeyPassword = '';
    this.passkeyError.set('');
    this.registeringPasskey.set(true);
    try {
      const opciones = await firstValueFrom(this.account.passkeyOptions(password));
      const credencial = await navigator.credentials.create(creationOptions(opciones.data)) as PublicKeyCredential | null;
      if (!credencial) throw new DOMException('Sin passkey', 'NotAllowedError');
      await firstValueFrom(this.account.addPasskey(registrationJSON(credencial), nickname));
      this.addingPasskey.set(false);
      this.toast.success('Passkey añadida.');
      this.loadPasskeys();
    } catch (e: any) {
      this.passkeyError.set(passkeyCancelled(e)
        ? 'No se creó la passkey. Si este dispositivo ya tiene una de tu cuenta, no hace falta otra.'
        : e?.error?.message || 'No se pudo añadir la passkey.');
    } finally {
      this.registeringPasskey.set(false);
    }
  }

  openRename(passkey: Passkey) {
    this.renameNickname = passkey.nickname;
    this.renaming.set(passkey);
  }

  closeRename = () => this.renaming.set(null);

  saveRename() {
    const passkey = this.renaming();
    if (!passkey) return;
    this.account.renamePasskey(passkey.id, this.renameNickname.trim()).subscribe({
      next: () => { this.renaming.set(null); this.loadPasskeys(); },
      error: e => this.toast.error(e?.error?.message || 'No se pudo renombrar la passkey.'),
    });
  }

  async deletePasskey(passkey: Passkey) {
    const confirmed = await this.confirm.ask({
      title: 'Eliminar passkey',
      message: `«${passkey.nickname}» dejará de servir para entrar. Tu contraseña y tus códigos de recuperación siguen igual.`,
      confirmLabel: 'Eliminar',
      danger: true,
    });
    if (!confirmed) return;
    this.account.deletePasskey(passkey.id).subscribe({
      next: () => { this.toast.success('Passkey eliminada.'); this.loadPasskeys(); },
      error: () => this.toast.error('No se pudo eliminar la passkey.'),
    });
  }

  loadRecoveryStatus() {
    this.account.recoveryStatus().subscribe({ next: r => this.recoveryStatus.set(r.data), error: () => {} });
  }

  openGenerate() {
    this.recoveryError.set('');
    this.askingRecoveryPassword.set(true);
  }

  confirmGenerate() {
    const password = this.recoveryPassword;
    this.recoveryPassword = '';
    this.generatingCodes.set(true);
    this.account.generateRecoveryCodes(password).subscribe({
      next: r => {
        this.generatingCodes.set(false);
        this.askingRecoveryPassword.set(false);
        this.revealedCodes.set(r.data.codes);
        this.loadRecoveryStatus();
      },
      error: e => {
        this.generatingCodes.set(false);
        this.recoveryError.set(e?.error?.message || 'No se pudieron generar los códigos.');
      },
    });
  }

  dismissCodes() { this.revealedCodes.set([]); }

  private codesText(): string {
    return ['AlbumFP - códigos de recuperación', `Cuenta: @${this.auth.user()?.username ?? ''}`,
      'Cada código sirve una sola vez. Guárdalos fuera de este dispositivo.', '', ...this.revealedCodes(), ''].join('\n');
  }

  copyCodes() {
    navigator.clipboard?.writeText(this.codesText()).then(
      () => this.toast.success('Códigos copiados.'),
      () => this.toast.error('No se pudieron copiar: descárgalos o cópialos a mano.'),
    );
  }

  /** Un archivo generado en memoria: la URL del Blob se revoca en el acto. */
  downloadCodes() {
    const url = URL.createObjectURL(new Blob([this.codesText()], { type: 'text/plain;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'albumfp-codigos-de-recuperacion.txt';
    link.click();
    URL.revokeObjectURL(url);
  }

  printCodes() {
    const ventana = window.open('', '_blank', 'width=480,height=640');
    if (!ventana) return;
    const pre = ventana.document.createElement('pre');
    pre.textContent = this.codesText();
    ventana.document.body.appendChild(pre);
    ventana.print();
    ventana.close();
  }

  async revokeCodes() {
    const confirmed = await this.confirm.ask({
      title: 'Revocar códigos de recuperación',
      message: 'Ninguno de tus códigos servirá para recuperar la cuenta. Podrás generar otros cuando quieras.',
      confirmLabel: 'Revocar',
      danger: true,
    });
    if (!confirmed) return;
    this.account.revokeRecoveryCodes().subscribe({
      next: () => { this.toast.success('Códigos revocados.'); this.loadRecoveryStatus(); },
      error: () => this.toast.error('No se pudieron revocar los códigos.'),
    });
  }

  /** Primero el resumen (no gasta cupo): así un «vuelve más tarde» se ve
   * aquí, y no como un error del servidor en una pestaña de descarga. */
  startExport() {
    const options = { ...this.exportOptions };
    this.exportChecking.set(true);
    this.exportError.set('');
    this.account.exportSummary(options).subscribe({
      next: r => {
        this.exportSummary.set(r.data);
        this.exportChecking.set(false);
        if (!r.data.allowed) {
          this.exportError.set(`Ya pediste varias copias hace poco. Vuelve a intentarlo en unos ${Math.ceil(r.data.retry_after / 60)} min.`);
          return;
        }
        this.openDownload(this.account.exportUrl(options));
      },
      error: () => {
        this.exportChecking.set(false);
        this.exportError.set('No pudimos preparar la exportación ahora. Inténtalo de nuevo.');
      },
    });
  }

  /** Una navegación normal: el navegador baja el ZIP en streaming y la página
   * no se mueve, porque la respuesta es un adjunto. */
  openDownload(url: string) { window.location.assign(url); }
  closePassword = () => this.changingPassword.set(false);

  ngOnInit() {
    // El guard ya cargó un usuario válido con ensureSession(); esto solo lo
    // refresca. Si falla, se sigue mostrando el que ya está en memoria.
    this.auth.me().subscribe({ next: r => this.auth.user.set(r.data), error: () => {} });
    this.auth.stats().subscribe({
      next: r => this.stats.set(r.data),
      error: () => this.toast.error('No se pudieron cargar tus estadísticas.'),
    });
    this.loadSessions();
    this.loadRecoveryStatus();
    this.loadPasskeys();
    this.notifications.preferences().subscribe({
      next: r => this.prefs.set(r.data),
      error: () => this.toast.error('No se pudieron cargar tus preferencias de notificaciones.'),
    });
  }

  /** PATCH parcial de UNA categoría. Si falla, el interruptor vuelve a su
   * valor -- también en el DOM: el clic ya lo cambió, y si la señal vuelve al
   * mismo valor antes de repintar, Angular no tendría nada que corregir. */
  togglePref(key: keyof NotificationPreferences, input: HTMLInputElement) {
    const previous = this.prefs();
    if (!previous) return;
    const value = input.checked;
    this.prefs.set({ ...previous, [key]: value });
    this.savingPref.set(key);
    this.notifications.updatePreferences({ [key]: value }).subscribe({
      next: r => { this.prefs.set(r.data); this.savingPref.set(null); },
      error: () => {
        this.prefs.set(previous);
        input.checked = previous[key];
        this.savingPref.set(null);
        this.toast.error('No se pudo guardar la preferencia.');
      },
    });
  }

  loadSessions() {
    this.auth.sessions().subscribe({
      next: r => this.sessions.set(r.data),
      error: () => this.toast.error('No se pudieron cargar tus sesiones.'),
    });
  }

  /** La sesión actual se cierra por el flujo normal de logout: el servidor
   * ya limpia la cookie de este navegador, y sin eso la app se quedaría
   * pintando una sesión que dejó de valer. */
  async revokeCurrentSession() {
    const confirmed = await this.confirm.ask({
      title: 'Cerrar esta sesión',
      message: 'Vas a cerrar sesión en este dispositivo.',
      confirmLabel: 'Cerrar sesión',
    });
    if (!confirmed) return;
    this.auth.logout().subscribe(() => this.router.navigateByUrl('/login'));
  }

  async revokeSession(session: Session) {
    if (session.current) return this.revokeCurrentSession();

    const confirmed = await this.confirm.ask({
      title: 'Cerrar esta sesión',
      message: `Se cerrará la sesión de ${session.user_agent_summary || 'ese dispositivo'}.`,
      confirmLabel: 'Cerrar sesión',
      danger: true,
    });
    if (!confirmed) return;

    this.revokingSessionId.set(session.id);
    this.auth.revokeSession(session.id).subscribe({
      next: () => {
        this.revokingSessionId.set(null);
        this.sessions.update(list => list.filter(s => s.id !== session.id));
        this.toast.success('Sesión cerrada.');
      },
      error: error => {
        this.revokingSessionId.set(null);
        this.toast.error(error?.error?.message || 'No se pudo cerrar esa sesión.');
      },
    });
  }

  async revokeOtherSessions() {
    const confirmed = await this.confirm.ask({
      title: 'Cerrar sesión en los demás dispositivos',
      message: 'Se cerrará tu sesión en todos los dispositivos menos en este.',
      confirmLabel: 'Cerrar las demás',
      danger: true,
    });
    if (!confirmed) return;

    this.revokingOthers.set(true);
    this.auth.revokeOtherSessions().subscribe({
      next: () => {
        this.revokingOthers.set(false);
        this.sessions.update(list => list.filter(s => s.current));
        this.toast.success('Se cerró tu sesión en los demás dispositivos.');
      },
      error: error => {
        this.revokingOthers.set(false);
        this.toast.error(error?.error?.message || 'No se pudieron cerrar las otras sesiones.');
      },
    });
  }

  openEdit() {
    const user = this.auth.user();
    this.editFullName = user?.full_name || '';
    this.editUsername = user?.username || '';
    this.pickedAvatar = null;
    this.avatarPreview.set('');
    this.editing.set(true);
  }

  pickAvatar(event: Event) {
    const file = (event.target as HTMLInputElement).files?.[0] || null;
    this.pickedAvatar = file;
    this.avatarPreview.set(file ? URL.createObjectURL(file) : '');
  }

  saveAccount() {
    const full_name = this.editFullName.trim();
    const username = this.editUsername.trim();
    if (!full_name || !username) {
      this.toast.error('El nombre y el usuario no pueden quedar vacíos.');
      return;
    }

    this.saving.set(true);
    const finish = () => {
      this.saving.set(false);
      this.editing.set(false);
      // El nombre de usuario aparece en las URL de perfil público, así que la
      // caché de lecturas deja de ser válida cuando cambia.
      this.api.invalidate();
      this.toast.success('Perfil actualizado.');
    };

    this.auth.updateMe({ username, full_name }).subscribe({
      next: () => {
        if (!this.pickedAvatar) return finish();
        this.auth.uploadAvatar(this.pickedAvatar).subscribe({
          next: () => finish(),
          error: error => {
            this.saving.set(false);
            this.toast.error(error?.error?.message || 'No se pudo subir la foto.');
          },
        });
      },
      error: error => {
        this.saving.set(false);
        this.toast.error(error?.error?.message || 'No se pudo actualizar el perfil.');
      },
    });
  }

  savePassword() {
    if (!this.currentPassword || !this.newPassword) {
      this.toast.error('Completa la contraseña actual y la nueva.');
      return;
    }
    if (this.newPassword !== this.repeatPassword) {
      this.toast.error('La contraseña nueva no coincide con su repetición.');
      return;
    }

    this.saving.set(true);
    this.auth.changePassword({ current_password: this.currentPassword, new_password: this.newPassword }).subscribe({
      next: () => {
        this.saving.set(false);
        this.changingPassword.set(false);
        this.currentPassword = this.newPassword = this.repeatPassword = '';
        this.toast.success('Contraseña actualizada. Cerramos tu sesión en los demás dispositivos.');
        // El backend acaba de rotar la sesion actual (nuevo id, nueva
        // cookie) y revocar las demas: la lista que ya se veia quedo vieja.
        this.loadSessions();
      },
      error: error => {
        this.saving.set(false);
        this.toast.error(error?.error?.message || 'No se pudo cambiar la contraseña.');
      },
    });
  }

  async deleteAccount() {
    const username = this.auth.user()?.username;
    if (!username) return;

    const confirmed = await this.confirm.ask({
      title: 'Eliminar mi cuenta',
      message: 'Se eliminarán tu cuenta, todos tus álbumes y todas tus fotos y videos de forma permanente. Nada pasará por la papelera y no se podrá recuperar.',
      confirmLabel: 'Eliminar mi cuenta',
      danger: true,
      requireText: username,
    });
    if (!confirmed) return;

    this.auth.deleteMe(username).subscribe({
      next: () => {
        // La cuenta ya no existe, y sus sesiones se fueron con ella por
        // cascada: no hay nada que revocar, solo estado local que limpiar.
        this.auth.clearLocalState();
        this.router.navigateByUrl('/');
      },
      error: error => this.toast.error(error?.error?.message || 'No se pudo eliminar la cuenta.'),
    });
  }
}
