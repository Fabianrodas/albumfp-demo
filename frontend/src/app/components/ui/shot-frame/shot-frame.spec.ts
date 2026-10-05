import { describe, expect, it } from 'vitest';
import { CAPTURAS_REV, themedShotSrc } from './shot-frame';

describe('ShotFrame theme asset selection', () => {
  it('keeps the shipped local screenshot when no light counterpart exists', () => {
    expect(themedShotSrc('capturas/paso-inicio.webp', false))
      .toBe(`capturas/paso-inicio.webp?v=${CAPTURAS_REV}`);
  });

  it('uses a shipped light counterpart for a phone screenshot', () => {
    expect(themedShotSrc('capturas/movil/album.webp', false))
      .toBe(`capturas/claro/movil/album.webp?v=${CAPTURAS_REV}`);
  });

  it('keeps the dark screenshot in dark mode', () => {
    expect(themedShotSrc('capturas/movil/album.webp', true))
      .toBe(`capturas/movil/album.webp?v=${CAPTURAS_REV}`);
  });
});
