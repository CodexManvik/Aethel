import { useEffect, useState } from "react";
import { apiBlob } from "./api";

/** An object URL for an authenticated backend file (<img> can't send the auth header itself); revoked on change. */
export function useObjectUrl(path: string | null): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return setUrl(null);
    let alive = true;
    let made: string | null = null;
    apiBlob(path).then((blob) => {
      if (!alive) return;
      made = URL.createObjectURL(blob);
      setUrl(made);
    }).catch(() => alive && setUrl(null));
    return () => {
      alive = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [path]);
  return url;
}
