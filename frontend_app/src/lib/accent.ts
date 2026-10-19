/** The active persona's accent colour, light and dark, on <html>; tokens.css picks the one for the theme. No
 * accent (the built-in Aethel, or a persona without a portrait yet) leaves the default terracotta. */
export function applyPersonaAccent(accent: { light: string; dark: string } | null | undefined): void {
  const root = document.documentElement;
  if (accent) {
    root.style.setProperty("--accent-light", accent.light);
    root.style.setProperty("--accent-dark", accent.dark);
    root.dataset.personaAccent = "";
  } else {
    root.style.removeProperty("--accent-light");
    root.style.removeProperty("--accent-dark");
    delete root.dataset.personaAccent;
  }
}
