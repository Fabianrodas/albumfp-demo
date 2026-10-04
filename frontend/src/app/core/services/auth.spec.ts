import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { AlbumApi } from './album-api';
import { Auth } from './auth';

describe('Auth session end', () => {
  let api: AlbumApi;
  let auth: Auth;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    api = TestBed.inject(AlbumApi);
    auth = TestBed.inject(Auth);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  // Cerrar sesión no recarga la página: sin esto, la siguiente cuenta que entre
  // en la misma pestaña recibiría de memoria los listados de la anterior.
  it('drops memoized reads so the next account in this tab refetches', () => {
    api.albums().subscribe();
    http.expectOne('/api/albums').flush({ data: [{ id: 1 }], message: 'ok' });

    auth.clearLocalState();

    api.albums().subscribe();
    http.expectOne('/api/albums').flush({ data: [], message: 'ok' });
  });

  it('forgets downloaded previews too', () => {
    api.mediaPreview(7).subscribe();
    http.expectOne('/api/media/7/preview').flush(new Blob(['x']));

    auth.clearLocalState();

    api.mediaPreview(7).subscribe();
    http.expectOne('/api/media/7/preview').flush(new Blob(['x']));
  });
});
