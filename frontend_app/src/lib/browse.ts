import { inTauri } from "./backend";

export interface BrowseFilter {
  name: string;
  extensions: string[];
}

/** The OS "open file" dialog in the app; null when cancelled, or in browser dev (where the path is typed instead). */
export async function browseFile(title: string, filters?: BrowseFilter[], directory = false): Promise<string | null> {
  if (!inTauri()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const picked = await open({ title, filters, directory, multiple: false });
  return typeof picked === "string" ? picked : null;
}

export const canBrowse = inTauri;
