import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { Media } from '../../../core/services/album-api';
import { MediaGrid } from './media-grid';

/** Abre un recuerdo en el modo dado y devuelve los argumentos de navegación. */
function openIn(mode: string, media: Media) {
  TestBed.configureTestingModule({ imports: [MediaGrid], providers: [provideRouter([])] });
  const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
  const fixture = TestBed.createComponent(MediaGrid);
  fixture.componentRef.setInput('items', [media]);
  fixture.componentRef.setInput('mode', mode);

  fixture.componentInstance.openDetail(media);

  return navigate;
}

describe('MediaGrid library navigation', () => {
  it('opens a library item as the asset itself, ignoring its presentation album', () => {
    const media: Media = { id: 17, album_id: 4, file_type: 'image' };

    expect(openIn('library', media)).toHaveBeenCalledWith(['/recuerdos', 17], {
      queryParams: { from: 'library', focus: 17 },
    });
  });

  it('opens a library item that is in no album at all', () => {
    const media: Media = { id: 17, album_id: null, file_type: 'image' };

    expect(openIn('library', media)).toHaveBeenCalledWith(['/recuerdos', 17], {
      queryParams: { from: 'library', focus: 17 },
    });
  });

  it('opens archived media as the asset itself and keeps the focus to return to it', () => {
    const media: Media = { id: 31, album_id: 8, file_type: 'video', archived_at: '2026-09-01T10:00:00' };

    expect(openIn('archive', media)).toHaveBeenCalledWith(['/recuerdos', 31], {
      queryParams: { from: 'archive', focus: 31 },
    });
  });

  it('keeps the album context only when the album itself was opened', () => {
    const media: Media = { id: 17, album_id: 4, file_type: 'image' };

    expect(openIn('album', media)).toHaveBeenCalledWith(['/albumes', 4, 'media', 17], {
      queryParams: { from: 'album' },
    });
  });

  it.each(['favorites', 'home', 'places', 'search'])(
    'opens a %s item as the asset itself even with a context album',
    mode => {
      const media: Media = { id: 21, album_id: 8, file_type: 'video' };

      expect(openIn(mode, media)).toHaveBeenCalledWith(['/recuerdos', 21], {
        queryParams: { from: mode },
      });
    },
  );

  it('still carries the search filters so that going back rebuilds the query', () => {
    TestBed.configureTestingModule({ imports: [MediaGrid], providers: [provideRouter([])] });
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    const fixture = TestBed.createComponent(MediaGrid);
    const media: Media = { id: 5, album_id: 9, file_type: 'image' };
    fixture.componentRef.setInput('items', [media]);
    fixture.componentRef.setInput('mode', 'search');
    fixture.componentRef.setInput('backParams', { q: 'playa' });

    fixture.componentInstance.openDetail(media);

    expect(navigate).toHaveBeenCalledWith(['/recuerdos', 5], {
      queryParams: { from: 'search', q: 'playa' },
    });
  });

  it('opens a smart album result as the asset itself and remembers the smart album to return to', () => {
    TestBed.configureTestingModule({ imports: [MediaGrid], providers: [provideRouter([])] });
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    const fixture = TestBed.createComponent(MediaGrid);
    const media: Media = { id: 21, album_id: 8, file_type: 'image' };
    fixture.componentRef.setInput('items', [media]);
    fixture.componentRef.setInput('mode', 'smart');
    fixture.componentRef.setInput('backParams', { smart_album_id: '7' });

    fixture.componentInstance.openDetail(media);

    expect(navigate).toHaveBeenCalledWith(['/recuerdos', 21], {
      queryParams: { from: 'smart', smart_album_id: '7' },
    });
  });

  it('shows no favorite, tag, cover or trash action on a smart album result', () => {
    TestBed.configureTestingModule({ imports: [MediaGrid], providers: [provideRouter([])] });
    const fixture = TestBed.createComponent(MediaGrid);
    fixture.componentRef.setInput('items', [{ id: 21, album_id: 8, file_type: 'image', title: 'Foto' }] as Media[]);
    fixture.componentRef.setInput('mode', 'smart');
    fixture.componentRef.setInput('role', 'owner');
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelectorAll('.media-card__actions button').length).toBe(0);
  });

  it('marks archived media with a visible badge where it still appears', () => {
    TestBed.configureTestingModule({ imports: [MediaGrid], providers: [provideRouter([])] });
    const fixture = TestBed.createComponent(MediaGrid);
    fixture.componentRef.setInput('items', [
      { id: 1, album_id: 8, file_type: 'video', title: 'Guardada', archived_at: '2026-09-01T10:00:00' },
      { id: 2, album_id: 8, file_type: 'video', title: 'Activa', archived_at: null },
    ] as Media[]);
    fixture.componentRef.setInput('mode', 'album');
    fixture.detectChanges();

    const badges = fixture.nativeElement.querySelectorAll('.archived-badge');
    expect(badges.length).toBe(1);
    expect(badges[0].textContent).toContain('Archivado');
  });
});
