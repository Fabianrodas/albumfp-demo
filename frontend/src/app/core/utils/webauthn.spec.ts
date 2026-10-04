import { authenticationJSON, creationOptions, fromBase64url, requestOptions, toBase64url } from './webauthn';

const bytes = (...values: number[]) => new Uint8Array(values).buffer;

describe('webauthn encoding (L15)', () => {
  it('round-trips base64url without padding or unsafe characters', () => {
    const raw = bytes(0xfb, 0xff, 0xbf, 0x00, 0x01);
    const encoded = toBase64url(raw);
    expect(encoded).toBe('-_-_AAE');
    expect([...new Uint8Array(fromBase64url(encoded))]).toEqual([0xfb, 0xff, 0xbf, 0x00, 0x01]);
  });

  it('turns the server options into the binary shape navigator.credentials expects', () => {
    const create = creationOptions({
      challenge: 'AAEC', rp: { id: 'localhost', name: 'AlbumFP' },
      user: { id: 'AAAAAAAN6Ls', name: 'ana', displayName: 'Ana' },
      pubKeyCredParams: [], excludeCredentials: [{ id: 'CQo', type: 'public-key' }],
    }).publicKey!;
    expect([...new Uint8Array(create.challenge as ArrayBuffer)]).toEqual([0, 1, 2]);
    expect(create.user.name).toBe('ana');
    expect([...new Uint8Array(create.excludeCredentials![0].id as ArrayBuffer)]).toEqual([9, 10]);

    const get = requestOptions({ challenge: 'AAEC', rpId: 'localhost' }).publicKey!;
    expect(get.allowCredentials, 'login sin usuario: la lista viaja vacía').toEqual([]);
  });

  it('serialises an assertion with the user handle the server compares', () => {
    const credential = {
      id: 'CQo', rawId: bytes(9, 10), type: 'public-key', authenticatorAttachment: 'platform',
      getClientExtensionResults: () => ({}),
      response: { clientDataJSON: bytes(1), authenticatorData: bytes(2), signature: bytes(3), userHandle: bytes(4) },
    } as unknown as PublicKeyCredential;
    expect(authenticationJSON(credential)).toEqual({
      id: 'CQo', rawId: 'CQo', type: 'public-key', authenticatorAttachment: 'platform', clientExtensionResults: {},
      response: { clientDataJSON: 'AQ', authenticatorData: 'Ag', signature: 'Aw', userHandle: 'BA' },
    });
  });
});
