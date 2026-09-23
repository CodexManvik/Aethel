import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Rail } from "./Rail";
import { useUi } from "../../stores/ui";

beforeEach(() => useUi.setState({ screen: "conversation", threadsOpen: false, theme: "light", motion: "system" }));

test("settings button switches screen and back", async () => {
  render(<Rail />);
  await userEvent.click(screen.getByRole("button", { name: "Settings" }));
  expect(useUi.getState().screen).toBe("settings");
  await userEvent.click(screen.getByRole("button", { name: "Aethel" }));
  expect(useUi.getState().screen).toBe("conversation");
});

test("threads button toggles the threads panel", async () => {
  render(<Rail />);
  await userEvent.click(screen.getByRole("button", { name: "Conversations" }));
  expect(useUi.getState().threadsOpen).toBe(true);
});

test("theme toggle flips data-theme on <html>", async () => {
  render(<Rail />);
  await userEvent.click(screen.getByRole("button", { name: "Switch to dark theme" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(useUi.getState().theme).toBe("dark");
});
