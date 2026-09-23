import type { ButtonHTMLAttributes } from "react";
import { cn } from "./cn";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "quiet" | "danger";
}

export function Button({ variant = "quiet", className, type = "button", ...rest }: Props) {
  return (
    <button
      type={type}
      className={cn(
        "inline-flex h-9 items-center gap-2 rounded-full px-4 text-[13px] font-medium transition-colors duration-[var(--dur-fast)] disabled:opacity-40",
        variant === "primary" && "bg-ink text-canvas hover:opacity-90",
        variant === "quiet" && "border border-hairline bg-paper text-ink hover:border-muted",
        variant === "danger" && "border border-hairline bg-paper text-accent hover:border-accent",
        className,
      )}
      {...rest}
    />
  );
}
