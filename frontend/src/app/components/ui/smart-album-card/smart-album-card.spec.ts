import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { SmartAlbum } from '../../../core/services/album-api';
import { SmartAlbumCard } from './smart-album-card';

const smart = (overrides: Partial<SmartAlbum> = {}): SmartAlbum => ({
  id: 5,
  titulo: 'Favoritos 2026',
  descripcion: 'Lo mejor del año',
  filters: { year: 2026, favorite: true, tag_id: 9 },
  created_at: '2026-09-23T10:00:00',
  updated_at: null,
  references: { album: null, tag: { id: 9, name: 'Viaje' } },
  stale_references: [],
  ...overrides,
});

function render(value: SmartAlbum) {
  TestBed.configureTestingModule({ imports: [SmartAlbumCard], providers: [provideRouter([])] });
  const fixture = TestBed.createComponent(SmartAlbumCard);
  fixture.componentRef.setInput('smart', value);
  fixture.detectChanges();
  return fixture.nativeElement as HTMLElement;
}

describe('SmartAlbumCard', () => {
  it('links to the smart album route with a subtle "Inteligente" badge and the filter summary', () => {
    const card = render(smart());
    expect(card.querySelector('a')?.getAttribute('href')).toBe('/albumes/inteligentes/5');
    expect(card.textContent).toContain('Favoritos 2026');
    expect(card.querySelector('.smart-badge')?.textContent).toContain('Inteligente');
    expect(card.textContent).toContain('Año: 2026');
    expect(card.textContent).toContain('Etiqueta: Viaje');
  });

  it('flags a definition that references something that no longer exists without inventing a name', () => {
    const card = render(smart({ filters: { tag_id: 9 }, references: { album: null, tag: null }, stale_references: ['tag_id'] }));
    expect(card.textContent).toContain('Etiqueta no disponible');
    expect(card.querySelector('.smart-card__warning')).not.toBeNull();
  });
});
