import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useUi } from "../../stores/ui";
import { useTasks } from "../../stores/tasks";
import { useVoice } from "../../stores/voice";
import { CallView, Portrait, stateLine } from "./CallView";
import { PromptBox } from "../conversation/PromptBox";

const endCall = vi.fn(async () => {});
const toggleMute = vi.fn();

beforeEach(() => {
  endCall.mockClear();
  toggleMute.mockClear();
  useVoice.setState({ state: "listening", muted: false, captions: [], speaking: "", error: null, personaName: "Aethel",
    conversationId: "conv_1", endCall, toggleMute, outputLevel: () => 0 });
  useUi.setState({ callOpen: true, taskPanelId: null });
  useTasks.setState({ tasks: {} });
});

test("the state line says what is happening in plain words", () => {
  expect(stateLine("listening", false)).toBe("Listening…");
  expect(stateLine("hearing", false)).toBe("Listening…");
  expect(stateLine("thinking", false)).toBe("Thinking…");
  expect(stateLine("speaking", false)).toBe("");
  expect(stateLine("speaking", true)).toBe("Muted");
  expect(stateLine("ended", false)).toBe("Call ended");
});

test("each state renders, with the sentence being spoken and the user's last words", () => {
  useVoice.setState({ state: "speaking", speaking: "The library opens at nine.",
    captions: [{ role: "user", text: "when does it open", final: true }] });
  render(<CallView />);
  expect(screen.getByText("opens")).toBeInTheDocument();
  expect(screen.getByText("when does it open")).toBeInTheDocument();
  expect(screen.getByText("A")).toBeInTheDocument(); // the monogram until Phase 5's portraits
  expect(screen.getByRole("button", { name: "End call" })).toBeInTheDocument();
});

test("an ended call offers Close instead of the call controls", () => {
  useVoice.setState({ state: "error", error: "The microphone isn't available." });
  render(<CallView />);
  expect(screen.getByRole("alert")).toHaveTextContent("microphone");
  expect(screen.queryByRole("button", { name: "End call" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
});

test("the rings follow the level", () => {
  const { rerender } = render(<Portrait name="Aethel" level={0} />);
  expect(screen.getByTestId("portrait").dataset.level).toBe("0.00");
  rerender(<Portrait name="Aethel" level={0.75} />);
  expect(screen.getByTestId("portrait").dataset.level).toBe("0.75");
});

test("Space mutes and Esc ends the call", async () => {
  render(<CallView />);
  await userEvent.keyboard(" ");
  expect(toggleMute).toHaveBeenCalledTimes(1);
  await userEvent.keyboard("{Escape}");
  expect(endCall).toHaveBeenCalled();
  expect(useUi.getState().callOpen).toBe(false);
});

test("the task strip shows an active task of this conversation and opens its panel", async () => {
  useTasks.setState({ tasks: { t1: { id: "t1", conversationId: "conv_1", goal: "Open Notepad", state: "running", plan: ["a", "b"],
    planDone: [0], checks: [], steps: [{ id: "s", tool: "x", summary: "Opening Notepad", verdict: "allow", ok: null, detail: "", durationMs: null }],
    notes: [], approvals: [], summary: null, error: null } } });
  render(<CallView />);
  expect(screen.getByTestId("task-strip")).toHaveTextContent("Open NotepadOpening Notepad1/2");
  await userEvent.click(screen.getByTestId("task-strip"));
  expect(useUi.getState()).toMatchObject({ taskPanelId: "t1", callOpen: false });
});

test("Show chat hides the screen but keeps the call", async () => {
  render(<CallView />);
  await userEvent.click(screen.getByRole("button", { name: "Show chat" }));
  expect(useUi.getState().callOpen).toBe(false);
  expect(endCall).not.toHaveBeenCalled();
});

test("the prompt box's mic button starts a call", async () => {
  const onCall = vi.fn();
  render(<PromptBox onSend={() => {}} onStop={() => {}} streaming={false} onCall={onCall} />);
  await userEvent.click(screen.getByRole("button", { name: "Start a call" }));
  expect(onCall).toHaveBeenCalled();
});
