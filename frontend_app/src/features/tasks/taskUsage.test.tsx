import { render, screen } from "@testing-library/react";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), api: apiMock }));
import { TaskUsage } from "./TaskUsage";

test("shows the task's tokens and refetches when the task moves", async () => {
  apiMock.mockResolvedValue({ prompt: 4200, completion: 300, cached: 0, calls: 3, estimated: false });
  const { rerender } = render(<TaskUsage taskId="t1" version="running:2" />);
  expect(await screen.findByLabelText("Tokens used")).toHaveTextContent("4.2k in · 300 out · 3 calls");
  rerender(<TaskUsage taskId="t1" version="running:3" />);
  expect(apiMock).toHaveBeenCalledTimes(2);
  expect(apiMock).toHaveBeenCalledWith("/api/tasks/t1/usage");
});

test("nothing before the first call", async () => {
  apiMock.mockResolvedValue({ prompt: 0, completion: 0, cached: 0, calls: 0, estimated: false });
  const { container } = render(<TaskUsage taskId="t2" version="planning:0" />);
  await Promise.resolve();
  expect(container).toBeEmptyDOMElement();
});
