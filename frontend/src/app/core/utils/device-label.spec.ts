import { describeDevice } from './device-label';

describe('describeDevice (F06)', () => {
  it('names common browsers and systems, checking Edge/Opera before Chrome and Chrome before Safari', () => {
    const cases: [string, string][] = [
      ['Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36', 'Chrome · Windows'],
      ['Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36 Edg/140.0', 'Edge · Windows'],
      ['Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15', 'Safari · macOS'],
      ['Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1', 'Safari · iOS'],
      ['Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0', 'Firefox · Linux'],
      ['Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Mobile Safari/537.36', 'Chrome · Android'],
    ];
    for (const [ua, label] of cases) expect(describeDevice(ua), ua).toBe(label);
  });

  it('falls back sensibly for empty or non-browser clients', () => {
    expect(describeDevice(null)).toBe('Dispositivo desconocido');
    expect(describeDevice('')).toBe('Dispositivo desconocido');
    expect(describeDevice('python-httpx/0.28.1')).toBe('python-httpx/0.28.1');
  });
});
