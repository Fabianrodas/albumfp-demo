// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { isCurrentMediaRequest, mediaDetailLink, navigationKey, neighboringMediaId } from './media-navigation';

describe('neighboringMediaId', () => {
  const media = [{ id: 14 }, { id: 28 }, { id: 42 }];

  it('returns the adjacent item in the displayed order', () => {
    expect(neighboringMediaId(media, 28, 'previous')).toBe(14);
    expect(neighboringMediaId(media, 28, 'next')).toBe(42);
  });

  it('does not navigate past either end or from an unknown item', () => {
    expect(neighboringMediaId(media, 14, 'previous')).toBeNull();
    expect(neighboringMediaId(media, 42, 'next')).toBeNull();
    expect(neighboringMediaId(media, 99, 'next')).toBeNull();
  });
});

describe('navigationKey', () => {
  it('maps arrow keys outside editable controls to detail navigation', () => {
    expect(navigationKey('ArrowLeft', document.createElement('button'))).toBe('previous');
    expect(navigationKey('ArrowRight', document.createElement('div'))).toBe('next');
  });

  it('leaves arrow keys to text-entry controls', () => {
    expect(navigationKey('ArrowLeft', document.createElement('input'))).toBeNull();
    expect(navigationKey('ArrowRight', document.createElement('textarea'))).toBeNull();
  });

  it('does not navigate while a detail dialog owns the interaction', () => {
    expect(navigationKey('ArrowLeft', document.createElement('button'), true)).toBeNull();
    expect(navigationKey('ArrowRight', document.createElement('div'), true)).toBeNull();
  });
});

describe('isCurrentMediaRequest', () => {
  it('rejects an old request after returning from A to B and back to A', () => {
    expect(isCurrentMediaRequest(14, 3, 14, 5)).toBe(false);
    expect(isCurrentMediaRequest(14, 5, 14, 5)).toBe(true);
  });
});

describe('mediaDetailLink', () => {
  it('uses the album route with a context album and the photo route without one', () => {
    expect(mediaDetailLink(7, 41)).toEqual(['/albumes', 7, 'media', 41]);
    expect(mediaDetailLink(null, 41)).toEqual(['/recuerdos', 41]);
    expect(mediaDetailLink(undefined, 41)).toEqual(['/recuerdos', 41]);
  });
});
