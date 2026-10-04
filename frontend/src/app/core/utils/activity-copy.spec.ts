import { activityDetail, activityText, notificationLink, notificationText } from './activity-copy';
import { ActivityEntry, AppNotification } from '../services/notifications';
import { ALBUM_CAPABILITIES } from '../models/album-permissions';

// Las capacidades se nombran solo en album-permissions.ts (source_checks).
const [UPLOAD, , , ORGANIZE] = ALBUM_CAPABILITIES;

const ana = { id: 1, username: 'ana', full_name: 'Ariana López' };
const fab = { id: 2, username: 'fabian', full_name: '' };

function entry(event_type: ActivityEntry['event_type'], extra: Partial<ActivityEntry> = {}): ActivityEntry {
  return { id: 1, event_type, actor: ana, target: null, subject: null, details: {}, created_at: '2026-09-23T10:00:00', ...extra };
}

function notice(event_type: AppNotification['event_type'], extra: Partial<AppNotification> = {}): AppNotification {
  return { id: 5, event_type, album_id: 7, album_title: 'Viaje', actor: ana, read_at: null, created_at: '2026-09-23T10:00:00', ...extra };
}

describe('activity copy', () => {
  it('describes every activity event in plain Spanish, never with raw ids or event names', () => {
    const textos = [
      activityText(entry('album_invite_created', { target: fab })),
      activityText(entry('album_invite_created')),
      activityText(entry('share_claimed')),
      activityText(entry('asset_uploaded')),
      activityText(entry('asset_added')),
      activityText(entry('asset_removed')),
      activityText(entry('cover_changed')),
      activityText(entry('cover_changed', { details: { cleared: true } })),
      activityText(entry('share_permission_changed', { target: fab })),
    ];
    expect(textos).toEqual([
      'Ariana López invitó a fabian.',
      'Ariana López creó una invitación.',
      'Ariana López aceptó la invitación.',
      'Ariana López subió un recuerdo.',
      'Ariana López añadió un recuerdo al álbum.',
      'Ariana López quitó un recuerdo del álbum.',
      'Ariana López cambió la portada.',
      'Ariana López quitó la portada.',
      'Ariana López cambió los permisos de fabian.',
    ]);
    for (const texto of textos) expect(texto).not.toMatch(/_|\d{3,}|\{/);
  });

  it('falls back safely when the actor or the target account no longer exists', () => {
    expect(activityText(entry('asset_uploaded', { actor: null }))).toBe('Alguien subió un recuerdo.');
    expect(activityText(entry('share_permission_changed', { actor: null }))).toBe('Alguien cambió los permisos de un acceso.');
  });

  it('summarises the granted access with the shared capability labels', () => {
    expect(activityDetail(entry('album_invite_created', { details: { permission: 'read', capabilities: [] } })))
      .toBe('Solo lectura');
    expect(activityDetail(entry('share_permission_changed', { details: { permission: 'write', capabilities: [UPLOAD, ORGANIZE] } })))
      .toBe('Puede: Subir fotos y videos · Marcar favoritos y usar etiquetas');
    expect(activityDetail(entry('asset_added'))).toBe('');
  });

  it('describes notifications with their album and a safe fallback', () => {
    expect(notificationText(notice('album_invite'))).toBe('Ariana López te invitó a «Viaje».');
    expect(notificationText(notice('share_claimed'))).toBe('Ariana López aceptó tu invitación a «Viaje».');
    expect(notificationText(notice('shared_album_upload', { actor: null, album_title: null })))
      .toBe('Alguien subió un recuerdo a un álbum que ya no existe.');
  });

  it('routes each notification to a real screen, never to a deleted album', () => {
    expect(notificationLink(notice('album_invite'))).toBe('/compartido');
    expect(notificationLink(notice('share_claimed'))).toBe('/albumes/7');
    expect(notificationLink(notice('shared_album_upload'))).toBe('/albumes/7');
    expect(notificationLink(notice('shared_album_upload', { album_id: null }))).toBe('/albumes');
    expect(notificationLink(notice('share_claimed', { album_id: null }))).toBe('/albumes');
  });
});
