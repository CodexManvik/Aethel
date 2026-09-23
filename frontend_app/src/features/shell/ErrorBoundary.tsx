import { Component, type ErrorInfo, type ReactNode } from "react";

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Aethel UI error", error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="grain flex h-full flex-col items-center justify-center gap-4 bg-canvas p-8 text-ink">
        <h1 className="font-display text-4xl">Something tore.</h1>
        <pre className="max-w-xl overflow-auto rounded-lg border border-hairline bg-paper p-4 text-[12px] text-muted">
          {String(this.state.error)}
        </pre>
        <button className="rounded-full bg-ink px-4 py-2 text-[13px] text-canvas" onClick={() => window.location.reload()}>
          Reload
        </button>
      </div>
    );
  }
}
