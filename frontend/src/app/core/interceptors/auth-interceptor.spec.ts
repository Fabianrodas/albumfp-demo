import { TestBed } from '@angular/core/testing';
import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { authInterceptor } from './auth-interceptor';

describe('authInterceptor and the service worker (L16)', () => {
  function setup() {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(withInterceptors([authInterceptor])), provideHttpClientTesting()],
    });
    return { http: TestBed.inject(HttpClient), ctl: TestBed.inject(HttpTestingController) };
  }

  it('makes every app request bypass the service worker, GETs of private media included', () => {
    const { http, ctl } = setup();
    http.get('/api/media/7/preview').subscribe();
    http.post('/auth/logout', {}).subscribe();
    for (const req of ctl.match(() => true)) {
      expect(req.request.headers.get('ngsw-bypass'), req.request.url).toBe('true');
      req.flush({});
    }
  });

  it('adds nothing to an absolute URL', () => {
    const { http, ctl } = setup();
    http.get('https://example.test/x').subscribe();
    const req = ctl.expectOne('https://example.test/x');
    expect(req.request.headers.has('ngsw-bypass')).toBe(false);
    req.flush({});
  });
});
