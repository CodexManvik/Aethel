import { Boot } from "./features/shell/Boot";
import { AppShell } from "./features/shell/AppShell";

export function App() {
  return (
    <Boot>
      <AppShell />
    </Boot>
  );
}
