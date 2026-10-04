import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { NotificationsApi } from './notifications';

describe('NotificationsApi', () => {
  let api: NotificationsApi;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    api = TestBed.inject(NotificationsApi);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('talks only to the in-app notification endpoints', () => {
    api.list(2).subscribe();
    http.expectOne(r => r.url === '/api/notifications' && r.params.get('page') === '2' && r.params.get('per_page') === '20')
      .flush({ data: [], message: 'ok' });
    api.unreadCount().subscribe();
    http.expectOne('/api/notifications/unread-count').flush({ data: { unread: 0 }, message: 'ok' });
    api.markRead(9).subscribe();
    expect(http.expectOne('/api/notifications/9/read').request.method).toBe('PATCH');
    api.markAllRead().subscribe();
    expect(http.expectOne('/api/notifications/read-all').request.method).toBe('POST');
    api.updatePreferences({ notify_share_claimed: false }).subscribe();
    const patch = http.expectOne('/api/notifications/preferences');
    expect(patch.request.method).toBe('PATCH');
    expect(patch.request.body).toEqual({ notify_share_claimed: false });
  });

  it('never memoizes: another account may have written since the last read', () => {
    api.unreadCount().subscribe();
    api.unreadCount().subscribe();
    expect(http.match('/api/notifications/unread-count').length).toBe(2);
    api.albumActivity(4, 1).subscribe();
    api.albumActivity(4, 1).subscribe();
    expect(http.match(r => r.url === '/api/albums/4/activity').length).toBe(2);
  });
});
