/**
 * L15. Solo traduce entre el JSON del servidor (base64url) y los objetos de
 * `navigator.credentials`. La criptografía la hace el autenticador y la
 * verificación el servidor (py_webauthn): aquí no hay ni una ni otra.
 * No se guarda nada: ni la credencial ni el challenge tocan ningún almacén.
 */

/** Algo de JSON que viene del servidor sin más forma que la de WebAuthn. */
type Json = Record<string, any>;

export function passkeysSupported(): boolean {
  return typeof window !== 'undefined' && typeof window.PublicKeyCredential === 'function'
    && !!navigator.credentials;
}

export function fromBase64url(value: string): ArrayBuffer {
  const base64 = value.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(value.length / 4) * 4, '=');
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

export function toBase64url(buffer: ArrayBuffer | null): string {
  if (!buffer) return '';
  let binary = '';
  for (const byte of new Uint8Array(buffer)) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

const descriptors = (list: Json[] | undefined) =>
  (list ?? []).map(d => ({ ...d, id: fromBase64url(d['id']) }) as PublicKeyCredentialDescriptor);

export function creationOptions(json: Json): CredentialCreationOptions {
  return { publicKey: {
    ...json,
    challenge: fromBase64url(json['challenge']),
    user: { ...json['user'], id: fromBase64url(json['user']['id']) },
    excludeCredentials: descriptors(json['excludeCredentials']),
  } as PublicKeyCredentialCreationOptions };
}

export function requestOptions(json: Json): CredentialRequestOptions {
  return { publicKey: {
    ...json,
    challenge: fromBase64url(json['challenge']),
    allowCredentials: descriptors(json['allowCredentials']),
  } as PublicKeyCredentialRequestOptions };
}

export function registrationJSON(credential: PublicKeyCredential): Json {
  const response = credential.response as AuthenticatorAttestationResponse;
  return {
    id: credential.id, rawId: toBase64url(credential.rawId), type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment ?? undefined,
    clientExtensionResults: credential.getClientExtensionResults(),
    response: {
      clientDataJSON: toBase64url(response.clientDataJSON),
      attestationObject: toBase64url(response.attestationObject),
      transports: typeof response.getTransports === 'function' ? response.getTransports() : [],
    },
  };
}

export function authenticationJSON(credential: PublicKeyCredential): Json {
  const response = credential.response as AuthenticatorAssertionResponse;
  return {
    id: credential.id, rawId: toBase64url(credential.rawId), type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment ?? undefined,
    clientExtensionResults: credential.getClientExtensionResults(),
    response: {
      clientDataJSON: toBase64url(response.clientDataJSON),
      authenticatorData: toBase64url(response.authenticatorData),
      signature: toBase64url(response.signature),
      userHandle: response.userHandle ? toBase64url(response.userHandle) : null,
    },
  };
}

/** Cancelar el diálogo del sistema (o no tener ninguna passkey) no es un
 * error que haya que explicar con detalle. */
export function passkeyCancelled(error: unknown): boolean {
  return error instanceof DOMException && (error.name === 'NotAllowedError' || error.name === 'AbortError');
}
