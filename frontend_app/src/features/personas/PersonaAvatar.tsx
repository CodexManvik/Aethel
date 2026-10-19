import { useObjectUrl } from "../../lib/useObjectUrl";
import { cn } from "../../ui/cn";
import type { Persona } from "./personasApi";

interface Props {
  persona: Pick<Persona, "id" | "name" | "built_in" | "anchors" | "accent">;
  size?: number;
  active?: boolean;
  glow?: number; // 0–1: the tone engine's energy, as a soft accent halo
  className?: string;
}

/** The portrait when there is one; otherwise a monogram (Æ for the built-in Aethel), tinted with the persona's accent. */
export function PersonaAvatar({ persona, size = 36, active = false, glow = 0, className }: Props) {
  const anchor = persona.anchors[0];
  const src = useObjectUrl(anchor ? `/api/personas/${persona.id}/files/${anchor}` : null);
  const tint = persona.accent ? `color-mix(in oklab, ${persona.accent.light} 18%, var(--paper))` : undefined;
  const rings = [
    active ? "0 0 0 2px var(--canvas), 0 0 0 3.5px var(--accent)" : "0 0 0 1px var(--hairline)",
    glow > 0 ? `0 0 ${Math.round(size * 0.45)}px ${Math.round(size * 0.08)}px color-mix(in oklab, var(--accent) ${Math.round(glow * 100)}%, transparent)` : null,
  ].filter(Boolean).join(", ");
  return (
    <span
      data-testid={`avatar-${persona.id}`}
      className={cn("grid shrink-0 place-items-center overflow-hidden rounded-full bg-paper font-display leading-none text-ink",
        "transition-shadow duration-[var(--dur-slow)]", className)}
      style={{ width: size, height: size, fontSize: size * 0.53, boxShadow: rings, background: src ? undefined : tint }}
    >
      {src ? <img src={src} alt="" className="size-full object-cover" /> : persona.built_in ? "Æ" : persona.name.slice(0, 1).toUpperCase()}
    </span>
  );
}
