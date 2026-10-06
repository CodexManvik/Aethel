import { Children } from "react";
import Markdown, { type Components } from "react-markdown";
import type { UiMessage } from "../../stores/session";
import { useUi } from "../../stores/ui";
import { ActivityLine } from "./ActivityLine";
import { SourceList, citeText } from "./Citations";
import { InkText } from "./InkText";
import { RecallFooter } from "./RecallFooter";

// Each renderer drops react-markdown's `node` prop so it never reaches the DOM.
const components: Components = {
  p: ({ node: _n, ...props }) => <p className="mb-3 last:mb-0" {...props} />,
  a: ({ node: _n, ...props }) =>
    props.title === "cite" ? ( // a footnote: citeText marks the [n] it turned into links
      <sup className="mx-px font-sans text-[0.7em]">
        <a className="text-accent no-underline hover:underline" target="_blank" rel="noreferrer" href={props.href}
          aria-label={`Source ${Children.toArray(props.children).join("")}`}>{props.children}</a>
      </sup>
    ) : (
      <a className="text-accent underline decoration-accent/40 underline-offset-2" target="_blank" rel="noreferrer" {...props} />
    ),
  ul: ({ node: _n, ...props }) => <ul className="mb-3 list-disc pl-5 last:mb-0" {...props} />,
  ol: ({ node: _n, ...props }) => <ol className="mb-3 list-decimal pl-5 last:mb-0" {...props} />,
  code: ({ node: _n, ...props }) => <code className="rounded bg-well px-1 py-0.5 font-mono text-[0.85em]" {...props} />,
  pre: ({ node: _n, ...props }) => <pre className="mb-3 overflow-x-auto rounded-lg border border-hairline bg-paper p-3 font-mono text-[13px] leading-5" {...props} />,
};

export function PersonaMessage({ message }: { message: UiMessage }) {
  const setScreen = useUi((s) => s.setScreen);
  return (
    <div className="max-w-[78%] font-voice text-[17px] leading-[1.55] text-ink-2">
      {message.status === "streaming" ? (
        message.content ? (
          <InkText text={message.content} />
        ) : (
          <span aria-label="Aethel is writing" className="inline-flex gap-1 pt-2">
            <span className="breathe size-1.5 rounded-full bg-faint" />
            <span className="breathe size-1.5 rounded-full bg-faint [animation-delay:200ms]" />
            <span className="breathe size-1.5 rounded-full bg-faint [animation-delay:400ms]" />
          </span>
        )
      ) : (
        message.content && (
          <Markdown components={components}>{message.sources ? citeText(message.content, message.sources) : message.content}</Markdown>
        )
      )}
      {message.status === "streaming" && message.activity && <ActivityLine text={message.activity} />}
      {message.status !== "streaming" && message.sources && <SourceList sources={message.sources} />}
      {message.status === "stopped" && <p className="mt-1 font-sans text-[12px] text-faint">stopped</p>}
      {message.recalled && message.status !== "streaming" && <RecallFooter recalled={message.recalled} />}
      {message.status === "error" && (
        <div className="mt-1 flex flex-wrap items-center gap-3 font-sans">
          <p role="alert" className="text-[13px] text-accent">
            {message.error ?? "Something went wrong."}
          </p>
          {message.errorCode === "no_provider" && (
            <button type="button" className="text-[12.5px] text-muted underline underline-offset-2 hover:text-ink" onClick={() => setScreen("settings")}>
              Open settings
            </button>
          )}
        </div>
      )}
    </div>
  );
}
