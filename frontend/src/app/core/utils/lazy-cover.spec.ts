import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component, Signal, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { lazyCoverUrl } from './lazy-cover';

type PortableLazyCover = (
  coverId: () => number | null | undefined,
  loadable: () => boolean,
) => Signal<string>;

@Component({ template: '<span>{{ url() }}</span>' })
class LazyCoverHost {
  readonly coverId = signal<number | null>(23);
  readonly loadable = signal(false);
  readonly url = (lazyCoverUrl as PortableLazyCover)(
    () => this.coverId(),
    () => this.loadable(),
  );
}

describe('lazyCoverUrl', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [LazyCoverHost],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
  });

  afterEach(() => TestBed.inject(HttpTestingController).verify());

  it('does not request a cover that the caller marks as a video', async () => {
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(LazyCoverHost);
    fixture.detectChanges();
    await fixture.whenStable();

    http.expectNone('/api/media/23/preview');
    fixture.destroy();
  });

  it('loads the same cover after it becomes an image', async () => {
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(LazyCoverHost);
    fixture.detectChanges();
    await fixture.whenStable();
    http.expectNone('/api/media/23/preview');

    fixture.componentInstance.loadable.set(true);
    fixture.detectChanges();
    http.expectOne('/api/media/23/preview').flush(new Blob(['preview']));
    fixture.destroy();
  });
});
