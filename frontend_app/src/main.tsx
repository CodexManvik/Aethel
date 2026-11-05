import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./fonts";
import "./styles/index.css";
import { App } from "./App";
import { ErrorBoundary } from "./features/shell/ErrorBoundary";
import { openLinksInBrowser } from "./lib/backend";
import { applyStoredAppearance } from "./stores/ui";
import { listenForKillSwitch } from "./features/tasks/taskActions";

applyStoredAppearance();
void listenForKillSwitch();
openLinksInBrowser();
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
