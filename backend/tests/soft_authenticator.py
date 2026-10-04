"""Un autenticador WebAuthn de software, SOLO para pruebas (L15).

Produce respuestas reales del protocolo (clientDataJSON, authenticatorData,
attestationObject `none` en CBOR, firma ECDSA P-256) para que las pruebas
ejerciten la verificación de verdad de `py_webauthn` en el servidor: origen,
RP ID, challenge, contador y firma. No es código de producción: el servidor
nunca implementa criptografía WebAuthn propia.
"""
import base64
import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SoftAuthenticator:
    def __init__(self, rp_id: str = "localhost", origin: str = "http://localhost:4200", *, counter: bool = True):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.rp_id, self.origin, self.counter = rp_id, origin, counter
        self.sign_count = 0
        self.user_handle = b""

    def _cose_key(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})

    def _client_data(self, kind: str, challenge: str, origin: str | None) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin or self.origin,
                           "crossOrigin": False}).encode()

    def create(self, options: dict, *, origin: str | None = None, rp_id: str | None = None, uv: bool = True) -> dict:
        self.user_handle = b64u_decode(options["user"]["id"])
        rp_hash = hashlib.sha256((rp_id or self.rp_id).encode()).digest()
        attested = (b"\x00" * 16 + struct.pack(">H", len(self.credential_id)) + self.credential_id
                    + self._cose_key())
        flags = 0x01 | (0x04 if uv else 0) | 0x40  # UP | UV? | AT
        auth_data = rp_hash + bytes([flags]) + struct.pack(">I", self.sign_count) + attested
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64u(self.credential_id), "rawId": b64u(self.credential_id), "type": "public-key",
            "response": {
                "clientDataJSON": b64u(self._client_data("webauthn.create", options["challenge"], origin)),
                "attestationObject": b64u(attestation), "transports": ["internal"],
            },
            "clientExtensionResults": {}, "authenticatorAttachment": "platform",
        }

    def get(self, options: dict, *, origin: str | None = None, rp_id: str | None = None,
            sign_count: int | None = None, user_handle: bytes | None = None, uv: bool = True) -> dict:
        if sign_count is None:
            if self.counter:
                self.sign_count += 1
            sign_count = self.sign_count
        rp_hash = hashlib.sha256((rp_id or self.rp_id).encode()).digest()
        auth_data = rp_hash + bytes([0x01 | (0x04 if uv else 0)]) + struct.pack(">I", sign_count)
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        signature = self.key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": b64u(self.credential_id), "rawId": b64u(self.credential_id), "type": "public-key",
            "response": {
                "clientDataJSON": b64u(client_data), "authenticatorData": b64u(auth_data),
                "signature": b64u(signature),
                "userHandle": b64u(self.user_handle if user_handle is None else user_handle),
            },
            "clientExtensionResults": {},
        }
