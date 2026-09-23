import type { UiMessage } from "../../stores/session";
import { cn } from "../../ui/cn";

export function UserMessage({ message }: { message: UiMessage }) {
  return (
    <div
      className={cn(
        "max-w-[70%] self-end whitespace-pre-wrap rounded-[16px_16px_4px_16px] bg-well px-3.5 py-2 text-[14px] leading-6 text-ink",
        message.status === "pending" && "opacity-70",
      )}
    >
      {message.content}
    </div>
  );
}
