/**
 * Portadas de video (v1.1): el fotograma lo saca el NAVEGADOR, que ya sabe
 * decodificar el video. El servidor no puede (FFmpeg rompería la regla de
 * portabilidad) y solo recibe una imagen que vuelve a validar y recodificar.
 *
 * Un video que este navegador no sabe decodificar (p. ej. HEVC en algunos
 * equipos) no da fotograma: la promesa se rechaza y el recuerdo se queda con
 * el marcador de video, igual que antes de v1.1.
 */

/** Lado mayor de la portada: el mismo tope que las vistas previas de fotos. */
export const POSTER_MAX_SIDE = 1280;

/** Un momento «al azar» que no sea el negro del primer fotograma ni los
 * créditos del final: entre el 15% y el 70% de la duración. */
export function defaultPosterTime(duration: number, random = Math.random): number {
  if (!Number.isFinite(duration) || duration <= 0) return 0;
  return +(duration * (0.15 + random() * 0.55)).toFixed(2);
}

export function posterSize(width: number, height: number, max = POSTER_MAX_SIDE) {
  const scale = Math.min(1, max / Math.max(width, height));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

function waitFor(video: HTMLVideoElement, event: 'loadedmetadata' | 'seeked', timeoutMs: number) {
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => done(new Error('timeout')), timeoutMs);
    const ok = () => done();
    const bad = () => done(new Error('decode'));
    function done(error?: Error) {
      window.clearTimeout(timer);
      video.removeEventListener(event, ok);
      video.removeEventListener('error', bad);
      if (error) reject(error); else resolve();
    }
    video.addEventListener(event, ok, { once: true });
    video.addEventListener('error', bad, { once: true });
  });
}

/** Duración real de `video`. Algunos WebM (los que graba un navegador o un
 * teléfono Android con MediaRecorder) no la llevan en la cabecera y el
 * navegador informa `Infinity` hasta que alguien busca el final: se le pide
 * ese salto una vez, se lee la duración y se vuelve al principio. */
export async function knownDuration(video: HTMLVideoElement, timeoutMs = 8_000): Promise<number> {
  if (Number.isFinite(video.duration)) return video.duration;
  const found = new Promise<void>(resolve => {
    const timer = window.setTimeout(done, timeoutMs);
    function check() { if (Number.isFinite(video.duration)) done(); }
    function done() { window.clearTimeout(timer); video.removeEventListener('durationchange', check); video.removeEventListener('timeupdate', check); resolve(); }
    video.addEventListener('durationchange', check);
    video.addEventListener('timeupdate', check);
  });
  video.currentTime = 1e101;
  await found;
  video.currentTime = 0;
  return Number.isFinite(video.duration) ? video.duration : 0;
}

/** Lleva `video` a `time` y devuelve ese fotograma como JPEG. El elemento
 * debe tener un `src` del mismo origen (o un blob:), o el canvas quedaría
 * contaminado y `toBlob` fallaría. */
export async function captureFrame(video: HTMLVideoElement, time: number, timeoutMs = 10_000): Promise<Blob> {
  if (video.readyState < HTMLMediaElement.HAVE_METADATA) await waitFor(video, 'loadedmetadata', timeoutMs);
  const target = Math.min(Math.max(0, time), Math.max(0, (video.duration || 0) - 0.05));
  // Buscar SIEMPRE que no haya fotograma a la vista: con `preload="metadata"`
  // el navegador puede no haber decodificado ninguno todavía, y un seek lo
  // obliga (al mismo tiempo también dispara `seeked`).
  if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || Math.abs(video.currentTime - target) > 0.01) {
    const seeked = waitFor(video, 'seeked', timeoutMs);
    video.currentTime = target;
    await seeked;
  }
  if (!video.videoWidth || !video.videoHeight) throw new Error('sin imagen');
  const size = posterSize(video.videoWidth, video.videoHeight);
  const canvas = document.createElement('canvas');
  canvas.width = size.width;
  canvas.height = size.height;
  canvas.getContext('2d')!.drawImage(video, 0, 0, size.width, size.height);
  return new Promise<Blob>((resolve, reject) =>
    canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('canvas')), 'image/jpeg', 0.86));
}
