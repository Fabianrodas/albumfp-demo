// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { readReturnTo } from './return-to';

describe('readReturnTo', () => {
  it('returns an in-app path with its label', () => {
    expect(readReturnTo({ back: '/recuerdos/7?from=library&focus=7', backLabel: 'Regresar al post' }))
      .toEqual({ back: '/recuerdos/7?from=library&focus=7', label: 'Regresar al post' });
  });

  it('refuses anything that could leave the app', () => {
    for (const back of ['//evil.test/x', '/\\evil.test', 'https://evil.test', 'javascript:alert(1)', '', 42]) {
      expect(readReturnTo({ back, backLabel: 'x' })).toBeNull();
    }
  });

  it('shows no link without navigation state', () => {
    expect(readReturnTo(null)).toBeNull();
    expect(readReturnTo({ navigationId: 3 })).toBeNull();
  });
});
