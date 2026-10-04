import { copyToClipboard } from './clipboard';

describe('copyToClipboard (F06)', () => {
  function withClipboard(value: unknown) {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value });
  }

  it('reports success', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    withClipboard({ writeText });
    expect(await copyToClipboard('https://demo.invalid/enlace/t')).toBe(true);
    expect(writeText).toHaveBeenCalledWith('https://demo.invalid/enlace/t');
  });

  it('turns a permission rejection into false instead of an uncaught NotAllowedError', async () => {
    withClipboard({ writeText: vi.fn().mockRejectedValue(new DOMException('denied', 'NotAllowedError')) });
    await expect(copyToClipboard('x')).resolves.toBe(false);
  });

  it('handles a browser without the clipboard API', async () => {
    withClipboard(undefined);
    await expect(copyToClipboard('x')).resolves.toBe(false);
  });
});
