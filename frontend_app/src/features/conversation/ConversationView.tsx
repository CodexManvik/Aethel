import { getSocket } from "../../lib/session";
import { useSession } from "../../stores/session";
import { useSettings } from "../settings/useSettings";
import { TaskPanel } from "../tasks/TaskPanel";
import { greeting } from "./greeting";
import { MessageList } from "./MessageList";
import { Notices } from "./Notices";
import { PromptBox } from "./PromptBox";
import { useSendMessage, useStartTask } from "./useConversations";

export function ConversationView() {
  const { conversationId, messages, streamingId, socketStatus } = useSession();
  const send = useSendMessage();
  const startTask = useStartTask();
  const { data: settings } = useSettings();
  const stop = () => streamingId && getSocket().send({ type: "stop_generation", message_id: streamingId });
  const status =
    socketStatus !== "open" ? "reconnecting…" : streamingId ? "writing…" : "here with you";

  const prompt = (
    <PromptBox
      onSend={(t, mode) => void (mode === "task" ? startTask(t) : send(t))}
      onStop={() => void stop()}
      streaming={!!streamingId}
    />
  );

  return (
    <div className="flex h-full bg-paper">
      <div className="flex min-w-0 flex-1 flex-col">
        <Notices />
        <header className="flex items-baseline gap-3 border-b border-hairline px-10 pt-6 pb-4">
          <span className="font-display text-[34px] leading-none">Aethel</span>
          <span className="flex items-center gap-1.5 text-[12px] text-muted">
            <span className={socketStatus === "open" ? "size-1.5 rounded-full bg-accent" : "breathe size-1.5 rounded-full bg-faint"} />
            {status}
          </span>
          {settings?.private_mode && (
            <span
              title="Private mode: everything stays on this computer"
              className="rounded-full border border-hairline px-2 py-px text-[11px] leading-4 text-muted"
            >
              private
            </span>
          )}
        </header>
        {conversationId === null && messages.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 px-10">
            <h1 className="font-display text-[48px] leading-none">{greeting()}</h1>
            <p className="mb-8 font-voice text-[18px] italic text-muted">What's on your mind?</p>
            {prompt}
          </div>
        ) : (
          <>
            <MessageList />
            <div className="px-10">{prompt}</div>
          </>
        )}
      </div>
      <TaskPanel />
    </div>
  );
}
