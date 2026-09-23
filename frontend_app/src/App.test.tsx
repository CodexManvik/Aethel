import { render, screen } from "@testing-library/react";
import { App } from "./App";

test("renders the Aethel wordmark", () => {
  render(<App />);
  expect(screen.getByText("Aethel")).toBeInTheDocument();
});
