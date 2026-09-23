# Aethel — Desktop App

The Tauri v2 + React front end for Aethel. For project setup, models, and how to
run everything together, see the [root README](../README.md) — normally you start
the app with `scripts/start.ps1` (or `scripts/start.sh`), which launches the
backend and this app together.

## Working on the UI alone

```bash
pnpm install          # pnpm is required (package.json uses pnpm overrides)
pnpm dev              # Vite dev server, browser only
pnpm tauri dev        # full desktop shell
pnpm build            # production bundle
```

The UI expects the backend at `http://localhost:8000`.

## Structure

```
src/
  app/
    App.tsx              shell: gate -> onboarding -> chat / admin
    components/
      ChatView.tsx       message feed + streaming
      PromptBox.tsx      composer: attachments, voice, Search/Tools/Speak pills
      Sidebar.tsx        conversations, status, navigation
      SettingsPanel.tsx  model pickers + every tunable, with hover help
      SkillReviewPanel.tsx / RuleReviewPanel.tsx   approve what the agent learns
      ui/                shadcn-style primitives + ToneGlow, FilePicker, Toast
    utils/
      useVoiceInput.ts       mic -> local Whisper transcription
      useMotionPreference.ts reduced-motion handling
  styles/theme.css       design tokens (the single source of truth)
```

## Theming

All colour, spacing, radius, shadow, and motion values are CSS custom properties
in `styles/theme.css`, split into a light and a dark block. Components read
`var(--v-*)` instead of branching on a theme flag, so adjusting or adding a theme
means editing one file.

Motion durations are tokens too (`--v-dur-*`), which lets `prefers-reduced-motion`
zero out every animation in one place rather than per component.

### Conversation tone

When the backend tone engine is on, `ToneGlow` wraps the composer: a light sweeps
around the border when the tone changes, then fades and leaves a soft ambient
colour behind the box. Tone colours are the `--v-tone-*` tokens.
