import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Media } from '../../../core/services/album-api';
import { MediaAsset } from './media-asset';

const video: Media = {
  id: 17,
  album_id: 4,
  file_type: 'video',
  title: 'Atardecer',
};

describe('MediaAsset video loading', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [MediaAsset],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
  });

  afterEach(() => TestBed.inject(HttpTestingController).verify());

  it('shows a video placeholder without downloading an original in preview quality', () => {
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(MediaAsset);
    fixture.componentRef.setInput('item', video);
    fixture.detectChanges();

    http.expectNone('/api/media/17/preview');
    http.expectNone('/api/media/17/file');
    expect(fixture.nativeElement.textContent).toContain('Video');
    expect(fixture.nativeElement.querySelector('.asset-video-placeholder')).toBeTruthy();
    fixture.destroy();
  });

  it('downloads the original when the detail explicitly requests original quality', () => {
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(MediaAsset);
    fixture.componentRef.setInput('item', video);
    fixture.componentRef.setInput('quality', 'original');
    fixture.detectChanges();

    http.expectOne('/api/media/17/file').flush(new Blob(['video']));
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('video')).toBeTruthy();
    fixture.destroy();
  });
});
