// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { POSTER_MAX_SIDE, defaultPosterTime, posterSize } from './video-frame';

describe('video poster helpers', () => {
  it('picks a moment inside 15%..70% of the video, never the black first frame', () => {
    expect(defaultPosterTime(100, () => 0)).toBe(15);
    expect(defaultPosterTime(100, () => 1)).toBe(70);
    expect(defaultPosterTime(Number.NaN)).toBe(0);
    expect(defaultPosterTime(0)).toBe(0);
  });

  it('caps the poster at the preview size and never upscales', () => {
    expect(posterSize(3840, 2160)).toEqual({ width: POSTER_MAX_SIDE, height: 720 });
    expect(posterSize(1080, 1920)).toEqual({ width: 720, height: POSTER_MAX_SIDE });
    expect(posterSize(640, 360)).toEqual({ width: 640, height: 360 });
  });
});
