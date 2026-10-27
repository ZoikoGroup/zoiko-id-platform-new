// @vitest-environment jsdom
/** The sensitive-action pattern (ZID-UX-001 §22, §26), exercised in a DOM with real keyboard/pointer events.
 *
 * Covers UX-25 (impact summary: dependents, reversibility, evidence consequence), UX-32 (the result shows
 * the canonical object, its revision and the receipt reference), UX-24 (a step-up in the middle of the flow
 * keeps what the user typed, and a retry reuses the idempotency key) and the commit gating that UX-05
 * relies on (a failed precondition, a missing step-up or a missing acknowledgement blocks the commit).
 * The backend remains the authority: nothing here proves what the server decides. */
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SensitiveActionDialog, type CommitContext, type CommitResult, type SensitiveActionProps } from "../src";

afterEach(cleanup);

// jsdom lacks CSS.escape, which react-aria's collection focus handling calls
if (typeof globalThis.CSS === "undefined" || !globalThis.CSS.escape) {
  (globalThis as { CSS?: unknown }).CSS = { ...(globalThis.CSS ?? {}), escape: (v: string) => v.replace(/([^\w-])/g, "\$1") };
}

const base = (over: Partial<SensitiveActionProps> = {}): SensitiveActionProps => ({
  isOpen: true,
  onOpenChange: () => {},
  title: "Revoke mandate M-441",
  intent: "Stops the agent from reserving budget.",
  risk: "R3",
  evidenceClass: "E3",
  reversibility: "irreversible",
  dependents: [
    { label: "Reservation R-1", effect: "Released" },
    { label: "Agent A-9", effect: "Loses its spending scope" },
  ],
  evidenceConsequence: "The decision receipt and audit record are kept and cannot be erased.",
  commitLabel: "Revoke mandate",
  onCommit: async () => ({ kind: "done", outcome: "REVOKED", objectId: "m-441", revision: 7, receiptId: "rcpt-0001-abcdef-01" }),
  ...over,
});

const next = (user: ReturnType<typeof userEvent.setup>) => user.click(screen.getByRole("button", { name: /continue to checks/i }));

describe("UX-25 destructive action: the impact summary says what depends on it, whether it can be undone and what evidence remains", () => {
  it("lists dependents with their effect, the reversibility and the evidence consequence before any commit", async () => {
    render(<SensitiveActionDialog {...base()} />);
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByRole("heading", { name: "Revoke mandate M-441" })).toBeTruthy();
    expect(within(dialog).getByText(/Dependent objects \(2\)/)).toBeTruthy();
    expect(within(dialog).getByText("Reservation R-1")).toBeTruthy();
    expect(within(dialog).getByText("Loses its spending scope")).toBeTruthy();
    expect(within(dialog).getByText("Irreversible")).toBeTruthy(); // reversibility is text, not colour
    expect(within(dialog).getByText(/cannot be erased/)).toBeTruthy(); // evidence consequence
    expect(within(dialog).getByText(/Evidence class E3, receipt available/)).toBeTruthy();
    expect(within(dialog).getByText(/Action risk R3/)).toBeTruthy();
  });

  it("a reversible action says so, and an irreversible one cannot be committed without acknowledgement", async () => {
    const onCommit = vi.fn(async (): Promise<CommitResult> => ({ kind: "done", outcome: "REVOKED", objectId: "m-441" }));
    const user = userEvent.setup();
    const { unmount } = render(<SensitiveActionDialog {...base({ reversibility: "reversible", onCommit })} />);
    expect(await screen.findByText("Reversible")).toBeTruthy();
    unmount();

    render(<SensitiveActionDialog {...base({ onCommit })} />);
    await next(user);
    const commit = screen.getByRole("button", { name: "Revoke mandate" }) as HTMLButtonElement;
    expect(commit.disabled).toBe(true);
    await user.click(screen.getByRole("checkbox", { name: /cannot be undone/i }));
    expect(commit.disabled).toBe(false);
    expect(onCommit).not.toHaveBeenCalled();
  });
});

describe("UX-32 evidence linkage: the result exposes the canonical object, its revision and the receipt", () => {
  it("shows outcome, object id, revision and receipt reference after the commit, and commits once with a key", async () => {
    const calls: CommitContext[] = [];
    const user = userEvent.setup();
    render(
      <SensitiveActionDialog
        {...base({
          expectedVersion: 6,
          onCommit: async (ctx) => {
            calls.push(ctx);
            return { kind: "done", outcome: "REVOKED", objectId: "m-441", revision: 7, receiptId: "rcpt-0001-abcdef-01", observe: [{ label: "Reservations", state: "RELEASED" }] };
          },
        })}
      />,
    );
    await next(user);
    await user.click(screen.getByRole("checkbox", { name: /cannot be undone/i }));
    await user.click(screen.getByRole("button", { name: "Revoke mandate" }));
    await screen.findByText("Propagation and dependent state");
    const result = screen.getAllByRole("status").find((n) => n.textContent?.includes("Outcome"))!;
    expect(result.textContent).toContain("Outcome: Revoked");
    expect(within(result).getByLabelText(/Copy object m-441/)).toBeTruthy(); // canonical object
    expect(within(result).getByLabelText("rev revision 7")).toBeTruthy(); // revision
    expect(within(result).getByLabelText(/Copy decision receipt rcpt-0001-abcdef-01/)).toBeTruthy(); // receipt reference
    expect(calls).toHaveLength(1);
    expect(calls[0]!.idempotencyKey).toMatch(/\S{8,}/);
    expect(calls[0]!.expectedVersion).toBe(6); // the commit is against the version the user looked at
  });

  it("a version conflict is shown as a conflict and nothing is reported as saved", async () => {
    const user = userEvent.setup();
    render(<SensitiveActionDialog {...base({ reversibility: "reversible", onCommit: async () => ({ kind: "conflict", expected: 6, current: 8 }) })} />);
    await next(user);
    await user.click(screen.getByRole("button", { name: "Revoke mandate" }));
    expect(await screen.findByText("This object changed while you were editing")).toBeTruthy();
    expect(screen.getByText(/Nothing was saved/)).toBeTruthy();
    expect(screen.queryByText(/Outcome: /)).toBeNull();
  });
});

describe("UX-24 draft preservation across a step-up", () => {
  it("keeps the typed note through a failed and a successful step-up, and a retry reuses the idempotency key", async () => {
    const keys: string[] = [];
    let attempt = 0;
    const perform = vi.fn(async () => perform.mock.calls.length > 1); // the first re-authentication fails (expired)
    const user = userEvent.setup();
    render(
      <SensitiveActionDialog
        {...base({
          reversibility: "reversible",
          reasons: [{ id: "SECURITY_INCIDENT", label: "Security incident" }],
          stepUp: { required: true, perform },
          onCommit: async (ctx) => {
            keys.push(ctx.idempotencyKey);
            attempt += 1;
            return attempt === 1 ? { kind: "error", error: new Error("network") } : { kind: "done", outcome: "REVOKED", objectId: "m-441" };
          },
        })}
      />,
    );
    await next(user);
    const note = screen.getByLabelText(/Note \(optional\)/) as HTMLTextAreaElement;
    await user.type(note, "Agent exfiltrated data on 12 Oct");
    await user.click(screen.getByRole("button", { name: /Reason code/ }));
    await user.click(await screen.findByRole("option", { name: "Security incident" }));

    const commit = screen.getByRole("button", { name: "Revoke mandate" }) as HTMLButtonElement;
    expect(commit.disabled).toBe(true); // no commit before the step-up
    await user.click(screen.getByRole("button", { name: /verify with passkey/i }));
    await waitFor(() => expect(perform).toHaveBeenCalledTimes(1));
    expect(commit.disabled).toBe(true); // the failed step-up unlocks nothing
    expect((screen.getByLabelText(/Note \(optional\)/) as HTMLTextAreaElement).value).toBe("Agent exfiltrated data on 12 Oct");

    await user.click(screen.getByRole("button", { name: /verify with passkey/i }));
    await waitFor(() => expect(commit.disabled).toBe(false));
    expect((screen.getByLabelText(/Note \(optional\)/) as HTMLTextAreaElement).value).toBe("Agent exfiltrated data on 12 Oct");

    await user.click(commit);
    await user.click(await screen.findByRole("button", { name: /review and try again/i }));
    expect((screen.getByLabelText(/Note \(optional\)/) as HTMLTextAreaElement).value).toBe("Agent exfiltrated data on 12 Oct"); // the draft survived the failure
    await user.click(screen.getByRole("button", { name: "Revoke mandate" }));
    await screen.findAllByText("Outcome: Revoked");
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]); // a retry can never apply the change twice
  });
});

describe("gating used by the policy-publish and approval flows (UX-05)", () => {
  it("a failed precondition blocks the commit and says why", async () => {
    const onCommit = vi.fn();
    const user = userEvent.setup();
    render(
      <SensitiveActionDialog
        {...base({
          reversibility: "reversible",
          onCommit,
          preconditions: [
            { label: "Validation cases pass", state: "pass" },
            { label: "Independent reviewer approved", state: "fail", detail: "Author cannot review their own release" },
          ],
        })}
      />,
    );
    await next(user);
    expect(screen.getByText("This action is blocked")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Revoke mandate" }) as HTMLButtonElement).disabled).toBe(true);
    expect(onCommit).not.toHaveBeenCalled();
  });

  it("when approval is required the button requests approval and the result says nothing changed yet", async () => {
    const user = userEvent.setup();
    render(
      <SensitiveActionDialog
        {...base({
          reversibility: "reversible",
          approval: { required: true, summary: "A second administrator must approve." },
          onCommit: async () => ({ kind: "approval-requested", approvalRequestId: "apr-1", approvers: "a second administrator" }),
        })}
      />,
    );
    await next(user);
    await user.click(screen.getByRole("button", { name: "Request approval" }));
    expect(await screen.findByText(/Nothing has changed yet/)).toBeTruthy();
  });
});
