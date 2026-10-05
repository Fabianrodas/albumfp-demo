import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { PublicMedia } from '../../../core/services/album-api';
import { PublicMediaCard } from './public-media-card';

const video: PublicMedia = {
  id: 23,
  album_id: 7,
  file_type: 'video',
  title: 'Atardecer',
  created_at: '2026-09-18T00:00:00Z',
  owner: { id: 2, username: 'ana', full_name: 'Ana', has_avatar: false },
};

describe('PublicMediaCard video placeholder', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [PublicMediaCard],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    });
  });

  afterEach(() => TestBed.inject(HttpTestingController).verify());

  it('shows its poster when it has one, and an accessible video marker when it does not', async () => {
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(PublicMediaCard);
    fixture.componentRef.setInput('media', video);
    fixture.detectChanges();
    await fixture.whenStable();

    // v1.1: la vista previa de un video es su portada; nunca se pide el original.
    http.expectOne('/api/media/23/preview').flush(new Blob(), { status: 404, statusText: 'Not Found' });
    http.expectNone('/api/media/23/file');
    fixture.detectChanges();
    const cover: HTMLAnchorElement = fixture.nativeElement.querySelector('.public-card__cover');
    expect(cover.getAttribute('aria-label')).toBe('Abrir video Atardecer');
    expect(fixture.nativeElement.querySelector('.public-card__video-placeholder')).toBeTruthy();
    expect(fixture.nativeElement.textContent).toContain('Video');
    fixture.destroy();
  });

  it('opens the detail with the explore origin so "back" returns to Explore', async () => {
    const fixture = TestBed.createComponent(PublicMediaCard);
    fixture.componentRef.setInput('media', video);
    fixture.detectChanges();
    await fixture.whenStable();
    TestBed.inject(HttpTestingController).match('/api/media/23/preview').forEach(r => r.flush(new Blob(['poster'])));

    const links = [...fixture.nativeElement.querySelectorAll('a[href*="/media/23"]')] as HTMLAnchorElement[];
    expect(links.length).toBe(2);
    for (const link of links) expect(link.getAttribute('href')).toBe('/albumes/7/media/23?from=explore');
    fixture.destroy();
  });
});
