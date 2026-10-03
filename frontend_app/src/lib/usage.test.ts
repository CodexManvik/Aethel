import { describeUsage, formatTokens } from "./usage";

test("token counts read naturally", () => {
  expect(formatTokens(950)).toBe("950");
  expect(formatTokens(12400)).toBe("12.4k");
  expect(describeUsage({ prompt: 12400, completion: 900, cached: 0, calls: 7, estimated: false })).toBe("12.4k in · 900 out · 7 calls");
  expect(describeUsage({ prompt: 1000, completion: 10, cached: 3100, calls: 1, estimated: true })).toBe("≈1.0k in · 10 out · 3.1k cached · 1 call");
});
