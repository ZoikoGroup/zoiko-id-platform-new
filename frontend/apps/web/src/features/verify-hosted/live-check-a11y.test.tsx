/** The hosted capture page in every step the applicant sees (ZID-CP-WF10 v2.0 row 6-04). Structural WCAG 2.2 AA checks with axe in a simulated browser, plus the keyboard path.
 * Colour contrast is not judged here (a simulated browser has no rendered colours), and a screen reader on real devices is separate evidence. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, describe, expect, it } from "vitest";

import { LiveCheck } from "./live-check";

afterEach(cleanup);

async function violations(container: HTMLElement) {
  const r = await axe.run(container, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] }, rules: { "color-contrast": { enabled: false } } });
  return r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

const ok = async (body: unknown) => {
  const action = (body as { action?: string }).action;
  if (action === "nonce") return { ok: true, data: { nonce: "n".repeat(16), attempts_left: 3 } };
  return { ok: true, data: { return_origin: null } };
};
const refuse = async () => ({ ok: false });

function view(call = ok) {
  return render(
    <main>
      <LiveCheck requestedBy="Acme Bank" noticeVersion="2026-11" attemptsLeft={3} call={call} />
    </main>,
  );
}

describe("the hosted capture page", () => {
  it("has no structural accessibility violation on the notice, with a heading and two named buttons", async () => {
    const { container } = view();
    expect(await violations(container)).toEqual([]);
    expect(screen.getByRole("heading", { level: 1, name: /before we start/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /continue/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /no thanks/i })).toBeTruthy();
  });

  it("has no violation on the document step, tells the person which step of two, and every control has a name", async () => {
    const user = userEvent.setup();
    const { container } = view();
    await user.click(screen.getByRole("button", { name: /continue/i }));
    await waitFor(() => expect(screen.getByText(/step 1 of 2/i)).toBeTruthy());
    expect(await violations(container)).toEqual([]);
    for (const b of screen.getAllByRole("button")) expect((b.textContent ?? "").trim() || b.getAttribute("aria-label")).toBeTruthy();
  });

  it("has no violation after the person declines, and says so in a status the screen reader announces", async () => {
    const user = userEvent.setup();
    const { container } = view();
    await user.click(screen.getByRole("button", { name: /no thanks/i }));
    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy());
    expect(await violations(container)).toEqual([]);
  });

  it("announces a failed send as an alert and stays free of violations", async () => {
    const user = userEvent.setup();
    const { container } = view(refuse);
    await user.click(screen.getByRole("button", { name: /continue/i }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(await violations(container)).toEqual([]);
  });

  it("can be used with the keyboard alone: the first control takes focus by Tab and Enter answers the notice", async () => {
    const user = userEvent.setup();
    view();
    await user.tab();
    expect((document.activeElement as HTMLElement).textContent).toMatch(/continue/i);
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByText(/step 1 of 2/i)).toBeTruthy());
  });
});
