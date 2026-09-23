import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cn } from "./cn";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  active?: boolean;
  children: ReactNode;
}

export const IconButton = forwardRef<HTMLButtonElement, Props>(function IconButton(
  { label, active, className, children, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      aria-label={label}
      title={label}
      className={cn(
        "grid size-9 place-items-center rounded-[10px] border border-transparent text-muted transition-colors duration-[var(--dur-fast)]",
        "hover:border-hairline hover:text-ink focus-visible:outline-2 focus-visible:outline-accent",
        active && "border-hairline bg-paper text-ink",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
});
