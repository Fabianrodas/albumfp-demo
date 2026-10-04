import { canDeleteMedia, canEditAlbum, canEditMedia, canManageAlbum, canOrganizeMedia, canUploadMedia, hasCapability } from './album-permissions';

describe('album permission helpers', () => {
  it('keeps sharing, trash and deactivating owner-only', () => {
    expect(canManageAlbum('owner')).toBe(true);
    expect(canManageAlbum('write')).toBe(false);
    expect(canManageAlbum('read')).toBe(false);
  });

  it('gives the owner every capability without needing a share row', () => {
    expect(hasCapability('owner', undefined, 'upload')).toBe(true);
    expect(hasCapability('owner', [], 'edit_album')).toBe(true);
  });

  it('gives a collaborator exactly what was granted, nothing adjacent', () => {
    const granted = ['upload', 'edit_media'];
    expect(canUploadMedia('write', granted)).toBe(true);
    expect(canEditMedia('write', granted)).toBe(true);
    expect(canDeleteMedia('write', granted)).toBe(false);
    expect(canOrganizeMedia('write', granted)).toBe(false);
    expect(canEditAlbum('write', granted)).toBe(false);
  });

  it('gives a read-only viewer nothing, even with a stray capability list', () => {
    expect(canUploadMedia('read', [])).toBe(false);
    expect(canEditAlbum('read', [])).toBe(false);
  });
});
