import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { api } from "../../lib/api";
import { useObjectUrl } from "../../lib/useObjectUrl";
import type { UiPhoto } from "../../stores/session";

const AVERAGE_KEY = "aethel_photo_ms";
const DEFAULT_MS = 12000;

/** How long a photo usually takes here: a running average kept in this browser (12 s until there is one). */
export function expectedMs(): number {
  try {
    const v = Number(localStorage.getItem(AVERAGE_KEY));
    return Number.isFinite(v) && v >= 1000 ? v : DEFAULT_MS;
  } catch {
    return DEFAULT_MS;
  }
}

export function recordDuration(ms: number): void {
  if (!(ms > 0)) return;
  try {
    const before = Number(localStorage.getItem(AVERAGE_KEY));
    const next = Number.isFinite(before) && before >= 1000 ? before * 0.7 + ms * 0.3 : ms;
    localStorage.setItem(AVERAGE_KEY, String(Math.round(next)));
  } catch {
    /* storage unavailable */
  }
}

const reduced = () => document.documentElement.dataset.motion === "reduced";

/** A photo in a reply (spec §10.4): a warm, blurred, sepia paper card while it develops, resolving over the usual
 * time; then the picture comes into focus. A failed one says so quietly and offers to try again. */
export function DevelopingPhoto({ photo }: { photo: UiPhoto }) {
  const src = useObjectUrl(photo.status === "ready" && photo.url ? photo.url : null);
  const [retrying, setRetrying] = useState(false);
  const [duration] = useState(expectedMs);
  const [developed, setDeveloped] = useState(false);
  const recorded = useRef(false);

  useEffect(() => {
    if (photo.status !== "pending") return;
    const t = requestAnimationFrame(() => setDeveloped(true)); // start the slow resolve after the first paint
    return () => cancelAnimationFrame(t);
  }, [photo.status]);
  useEffect(() => {
    if (photo.status === "ready" && photo.startedAt && !recorded.current) {
      recorded.current = true;
      recordDuration(Date.now() - photo.startedAt);
    }
  }, [photo.status, photo.startedAt]);

  if (photo.status === "failed") {
    return (
      <div className="mt-3 flex items-center gap-3 font-sans text-[12.5px] text-muted" data-testid="photo-failed">
        <span>The photo didn't come out.</span>
        <button type="button" disabled={retrying} className="text-accent underline underline-offset-2 disabled:opacity-50"
          onClick={() => { setRetrying(true); void api(`/api/images/${photo.id}/retry`, { method: "POST" }).catch(() => setRetrying(false)); }}>
          Try again
        </button>
      </div>
    );
  }

  const quiet = reduced();
  return (
    <figure className="mt-3 w-64 overflow-hidden rounded-lg border border-hairline bg-[#e9dcc6] shadow-[var(--lift)]"
      data-testid={`photo-${photo.status}`} data-duration={duration}>
      <div className="relative aspect-[3/4]">
        {src ? (
          <motion.img src={src} alt={photo.caption} className="absolute inset-0 size-full object-cover"
            initial={quiet ? { opacity: 0 } : { filter: "blur(4px)", scale: 1.02, opacity: 0.6 }}
            animate={quiet ? { opacity: 1 } : { filter: "blur(0px)", scale: 1, opacity: 1 }}
            transition={quiet ? { duration: 0.2 } : { type: "spring", stiffness: 120, damping: 20 }} />
        ) : (
          <div aria-label="A photo is developing" role="img" className="grain absolute inset-0"
            style={{
              background: "radial-gradient(120% 90% at 50% 35%, #f3e7d3, #d9c3a3)",
              filter: quiet ? undefined : `blur(${developed ? 6 : 14}px) sepia(0.4)`,
              transition: quiet ? undefined : `filter ${duration}ms linear`,
              opacity: quiet ? 0.85 : undefined,
            }} />
        )}
      </div>
      <figcaption className="px-3 py-2 font-voice text-[13.5px] italic leading-5 text-ink-2">{photo.caption}</figcaption>
    </figure>
  );
}
