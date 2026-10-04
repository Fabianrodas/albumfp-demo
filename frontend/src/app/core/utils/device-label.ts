/** Etiqueta legible de una sesión («Chrome · Windows») a partir del
 * user-agent que guarda el servidor tal cual (F06). Solo presentación: la
 * cadena completa sigue disponible como `title`. El orden de las comprobaciones
 * importa: Edge y Opera se anuncian también como Chrome, y Chrome como Safari. */
const BROWSERS: [RegExp, string][] = [
  [/Edg\//, 'Edge'], [/OPR\/|Opera/, 'Opera'], [/Firefox\/|FxiOS/, 'Firefox'],
  [/HeadlessChrome/, 'Chrome sin interfaz'], [/Chrome\/|CriOS/, 'Chrome'], [/Safari\//, 'Safari'],
];
const SYSTEMS: [RegExp, string][] = [
  [/iPhone|iPad|iOS/, 'iOS'], [/Android/, 'Android'], [/Windows/, 'Windows'],
  [/Mac OS X|Macintosh/, 'macOS'], [/CrOS/, 'ChromeOS'], [/Linux/, 'Linux'],
];

export function describeDevice(userAgent: string | null | undefined): string {
  const ua = (userAgent || '').trim();
  if (!ua) return 'Dispositivo desconocido';
  const browser = BROWSERS.find(([pattern]) => pattern.test(ua))?.[1];
  const system = SYSTEMS.find(([pattern]) => pattern.test(ua))?.[1];
  if (browser && system) return `${browser} · ${system}`;
  if (browser || system) return (browser || system)!;
  // Un cliente no navegador (una app, un script): su primer token basta.
  return ua.split(/[\s(]/)[0].slice(0, 40) || 'Dispositivo desconocido';
}
