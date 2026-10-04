/** Copia texto y dice si lo logró. El portapapeles puede negarse (permiso,
 * contexto no seguro, pestaña sin foco) o no existir: nunca deja una promesa
 * rechazada sin capturar (F06). */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (!navigator.clipboard?.writeText) return false;
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
