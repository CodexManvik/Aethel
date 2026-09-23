import { getSocket } from "../../lib/session";
import { useSession } from "../../stores/session";
import { greeting } from "./greeting";
import { MessageList } from "./MessageList";
import { Notices } from "./Notices";
import { PromptBox } from "./PromptBox";
import { useSendMessage } from "./useConversations";

export function ConversationView() {
  const { conversationId, messages, streamingId, socketStatus } = useSession();
  const send = useSendMessage();
  const stop = () => streamingId && getSocket().send({ type: "stop_generation", message_id: streamingId });
  const status =
    socketStatus !== "open" ? "reconnecting…" : streamingId ? "writing…" : "here with you";

  const prompt = <PromptBox onSend={(t) => void send(t)} onStop={() => void stop()} streaming={!!streamingId} />;

  return (
    <div className="flex h-full flex-col bg-paper">
      <Notices />
      <header className="flex items-baseline gap-3 border-b border-hairline px-10 pt-6 pb-4">
        <span className="font-display text-[34px] leading-none">Aethel</span>
        <span className="flex items-center gap-1.5 text-[12px] text-muted">
          <span className={socketStatus === "open" ? "size-1.5 rounded-full bg-accent" : "breathe size-1.5 rounded-full bg-faint"} />
          {status}
        </span>
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
  );
}
