(() => {
  try {
    const stored = localStorage.getItem('albumfp_theme');
    // Sin preferencia guardada se sigue la del sistema -- mismo criterio que
    // Theme.readInitialMode() en el servicio de Angular. Sin esto, quien
    // tuviera su equipo en oscuro veía un parpadeo a claro en cada carga
    // (este script pinta antes que Angular arranque) y luego un salto a
    // oscuro en cuanto el servicio corregía la preferencia real.
    const theme = stored === 'dark' || stored === 'light'
      ? stored
      : (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    // Mismo color que THEME_COLOR en core/services/theme.ts (el `--paper`).
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#101511' : '#f4f1ea');
  } catch {
    document.documentElement.dataset.theme = 'light';
    document.documentElement.style.colorScheme = 'light';
  }
})();
