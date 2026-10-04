import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { ApiResponse } from './album-api';

/** Grupos sensibles que solo viajan en la exportación si la persona los pide. */
export interface ExportOptions {
  exif: boolean;
  location: boolean;
  ocr: boolean;
}

export interface ExportSummary {
  assets: number;
  albums: number;
  total_bytes: number;
  largest_bytes: number;
  allowed: boolean;
  remaining: number;
  retry_after: number;
}

/** Una passkey de la cuenta: solo lo que la persona necesita para
 * reconocerla. Ni la clave pública ni el id de la credencial salen de aquí. */
export interface Passkey {
  id: number;
  nickname: string;
  created_at: string;
  last_used_at: string | null;
  transports: string[];
}

/** Estado de los códigos de recuperación: nunca los códigos. */
export interface RecoveryStatus {
  total: number;
  remaining: number;
  created_at: string | null;
}

function exportParams(options: ExportOptions): Record<string, string> {
  return { exif: String(options.exif), location: String(options.location), ocr: String(options.ocr) };
}

/**
 * Lo que la cuenta hace con sus propios datos y su propia seguridad. Sin la
 * memoria de `AlbumApi`: son lecturas que cambian con cada acción de la cuenta.
 */
@Injectable({ providedIn: 'root' })
export class AccountApi {
  private readonly http = inject(HttpClient);

  exportSummary(options: ExportOptions) {
    return this.http.get<ApiResponse<ExportSummary>>('/api/export/summary', { params: exportParams(options) });
  }

  /** El ZIP no pasa por HttpClient: lo baja el navegador, en streaming, sin
   * cargarlo entero en la memoria de la pestaña. */
  exportUrl(options: ExportOptions): string {
    return `/api/export/download?${new URLSearchParams(exportParams(options))}`;
  }

  // --- Códigos de recuperación (L14) --------------------------------------
  // Los códigos en claro solo llegan en la respuesta de `generateRecoveryCodes`
  // y no se guardan en ningún almacenamiento del navegador.
  recoveryStatus() { return this.http.get<ApiResponse<RecoveryStatus>>('/auth/recovery-codes'); }
  generateRecoveryCodes(currentPassword: string) {
    return this.http.post<ApiResponse<{ codes: string[]; created_at: string }>>(
      '/auth/recovery-codes', { current_password: currentPassword });
  }
  revokeRecoveryCodes() { return this.http.delete<ApiResponse<{ revoked: number }>>('/auth/recovery-codes'); }
  recover(body: { username: string; code: string; new_password: string }) {
    return this.http.post<ApiResponse<unknown>>('/auth/recover', body);
  }

  // --- Passkeys (L15) -------------------------------------------------------
  passkeys() { return this.http.get<ApiResponse<Passkey[]>>('/auth/passkeys'); }
  passkeyOptions(currentPassword: string) {
    return this.http.post<ApiResponse<Record<string, any>>>(
      '/auth/passkeys/register/options', { current_password: currentPassword });
  }
  addPasskey(credential: Record<string, any>, nickname: string) {
    return this.http.post<ApiResponse<Passkey>>('/auth/passkeys/register/verify', { credential, nickname });
  }
  renamePasskey(id: number, nickname: string) {
    return this.http.patch<ApiResponse<unknown>>(`/auth/passkeys/${id}`, { nickname });
  }
  deletePasskey(id: number) { return this.http.delete<ApiResponse<unknown>>(`/auth/passkeys/${id}`); }
}
