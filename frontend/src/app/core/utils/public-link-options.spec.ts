import { describe, expect, it } from 'vitest';
import { publicLinkOptions } from './public-link-options';

describe('publicLinkOptions', () => {
  it('preserves the explicit metadata choice alongside the other public-link settings', () => {
    expect(publicLinkOptions('  clave segura  ', true, false)).toEqual({
      password: 'clave segura',
      allow_original_download: true,
      show_metadata: false,
    });
  });

  it('turns a blank password into null without changing either privacy setting', () => {
    expect(publicLinkOptions('   ', false, true)).toEqual({
      password: null,
      allow_original_download: false,
      show_metadata: true,
    });
  });
});
