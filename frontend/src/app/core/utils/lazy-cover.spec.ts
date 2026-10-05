import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { lazyCoverUrl } from './lazy-cover';

@Component({ template: '<span>{{ url() }}</span>' })
class LazyCoverHost {
  readonly coverId = signal<number | null>(23);
  readonly url = lazyCoverUrl(() => this.coverId());
}

describe('lazyCoverUrl', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [LazyCoverHost],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
  });

  afterEach(() => TestBed.inject(HttpTestingController).verify());

  it('asks for the cover preview (a video cover gets its poster)', async () => {
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(LazyCoverHost);
    fixture.detectChanges();
    await fixture.whenStable();
    http.expectOne('/api/media/23/preview').flush(new Blob(['poster']));
    expect(fixture.componentInstance.url()).toMatch(/^blob:/);
    fixture.destroy();
  });

  it('leaves no URL when the cover has no preview, and loads a new cover when it changes', async () => {
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(LazyCoverHost);
    fixture.detectChanges();
    await fixture.whenStable();
    http.expectOne('/api/media/23/preview').flush(new Blob(), { status: 404, statusText: 'Not Found' });
    expect(fixture.componentInstance.url()).toBe('');

    fixture.componentInstance.coverId.set(24);
    fixture.detectChanges();
    http.expectOne('/api/media/24/preview').flush(new Blob(['preview']));
    expect(fixture.componentInstance.url()).toMatch(/^blob:/);
    fixture.destroy();
  });
});
