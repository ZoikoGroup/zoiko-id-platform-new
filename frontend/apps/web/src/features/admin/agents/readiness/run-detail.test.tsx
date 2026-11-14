/** Run detail (WF-AG-03): real @zoiko/ui tabs and dialog, mocked data. Redaction, refusal, a broken chain and the actions that only some runs offer. */
import { ApiError, type ReadinessDecision, type ReadinessEvidence, type ReadinessRunDetail, type ReadinessSetup } from "@zoiko/api-client";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ queries: {} as Record<string, unknown>, retry: vi.fn(), pause: vi.fn(), exported: vi.fn() }));

vi.mock("@/lib/cell", () => ({
  useCellQuery: (key: string[]) => {
    const which = key.includes("decisions") ? "decisions" : key.includes("evidence") ? "evidence" : key[3] === "setup" ? "setup" : "run";
    return state.queries[which] ?? { isPending: true, fetchStatus: "fetching" };
  },
  useCellMutation: (fn: (api: unknown, v: unknown) => unknown, opts: { success?: string }) => ({
    isPending: false,
    mutateAsync: (v: unknown) => {
      if (opts.success === "Retry queued") state.retry(v);
      if (opts.success === "Future runs paused") state.pause(v);
      if (opts.success === "Evidence exported") {
        state.exported(v);
        return Promise.resolve({ schema: "zid.access-readiness.evidence.v1", redacted: false });
      }
      return Promise.resolve({});
    },
  }),
  ApiErrorView: ({ error }: { error: Error }) => <div role="alert">api error: {error.message}</div>,
}));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: ReactNode }) => <a href={href}>{children}</a> }));

import { RunDetailPage } from "./run-detail";

// jsdom has no CSS.escape, which react-aria uses when a tab takes focus.
if (!(globalThis as { CSS?: unknown }).CSS) Object.defineProperty(globalThis, "CSS", { value: { escape: (v: string) => v.replace(/[^\w-]/g, (c) => `\\${c}`) } });

afterEach(cleanup);
beforeEach(() => {
  state.queries = {};
  state.retry.mockReset();
  state.pause.mockReset();
  state.exported.mockReset();
});

const err = (status: number, code: string) => new ApiError({ status, code, message: code });
const query = (over: object) => ({ isPending: false, isError: false, fetchStatus: "idle", data: undefined, refetch: vi.fn(), ...over }) as never;

const run = (over: Partial<ReadinessRunDetail> = {}): ReadinessRunDetail => ({
  run_id: "run-1",
  agent_id: "a1",
  state: "COMPLETED",
  attempt: 1,
  mode: "scheduled",
  purpose: "access_posture_review",
  resource_selector: {},
  denial_codes: [],
  summary: { records_read: 12, findings: 2, proposals_created: 1, proposals_existing: 0, privileges_changed: 0 },
  rule_set_version: "daara-rules/1",
  policy_hash: "hash-1",
  run_definition_hash: "def-1",
  region: "eu",
  trace_id: "trace-1",
  worker_attestation_id: "att-1",
  created_at: "2026-11-14T10:00:00Z",
  terminal_at: "2026-11-14T10:01:00Z",
  links: { self: "/x" },
  proposals: [],
  ...over,
});
const decision: ReadinessDecision = {
  decision_id: "d1",
  run_id: "run-1",
  actor: "agent",
  subject: "agent",
  resource: "mandate:abc",
  action: "read_mandate_metadata",
  purpose: "access_posture_review",
  outcome: "ALLOW",
  reason_code: "READ_PERMITTED",
  policy_hash: "hash-1",
  decided_at: "2026-11-14T10:00:10Z",
  trace_id: null,
};
const evidenceOk: ReadinessEvidence = {
  verified: true,
  integrity_exceptions: [],
  items: [
    { event_id: "e1", run_id: "run-1", seq: 1, event_type: "RUN_STARTED", source_id: "s", source_version: "1", payload_digest: "sha256:p", chain_digest: "sha256:c", classification: "INTERNAL", region: "eu", retention_policy_ref: "r", recorded_at: "2026-11-14T10:00:00Z" },
  ],
};
const setup = { status: "APPROVED", agent: { status: "ACTIVE", sponsor_active: true, attested: true, blueprint: "b@1" } } as unknown as ReadinessSetup;

function given(r: Partial<ReadinessRunDetail> = {}, evidence: unknown = query({ data: evidenceOk })) {
  state.queries.run = query({ data: run(r) });
  state.queries.decisions = query({ data: { items: [decision] } });
  state.queries.evidence = evidence;
  state.queries.setup = query({ data: setup });
  render(<RunDetailPage agentId="a1" runId="run-1" />);
}

describe("run detail", () => {
  it("switches between the four tabs with the keyboard", async () => {
    given();
    const user = userEvent.setup();
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Overview", "Policy Decisions", "Evidence", "Technical Events"]);
    expect(tabs[0]!.getAttribute("aria-selected")).toBe("true");
    tabs[0]!.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Policy Decisions" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("table", { name: "Policy decisions for this run" })).toBeTruthy();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Evidence" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("Evidence chain verified")).toBeTruthy();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Technical Events" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("Run definition hash")).toBeTruthy();
    await user.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: "Overview" }).getAttribute("aria-selected")).toBe("true");
  });

  it("shows the seven-step timeline in a labeled ordered list", () => {
    given();
    const list = screen.getByRole("list", { name: "Run timeline" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(7);
    expect(within(list).getByText(/Completed/)).toBeTruthy();
    expect(screen.getByText(/Checked 12 permitted records/)).toBeTruthy();
  });

  it("shows Redacted for your role, not an empty success, when the run is forbidden", () => {
    state.queries.run = query({ isError: true, error: err(403, "PROTECTED_ACTION_DENIED") });
    render(<RunDetailPage agentId="a1" runId="run-1" />);
    expect(screen.getByText("Redacted for your role")).toBeTruthy();
    expect(screen.queryByRole("tab")).toBeNull();
    expect(screen.queryByText(/Checked \d+ permitted records/)).toBeNull();
  });

  it("redacts only the evidence tab when evidence alone is forbidden", async () => {
    given({}, query({ isError: true, error: err(403, "PROTECTED_ACTION_DENIED") }));
    await userEvent.setup().click(screen.getByRole("tab", { name: "Evidence" }));
    expect(screen.getByText("Redacted for your role")).toBeTruthy();
    expect(screen.queryByText("Evidence chain verified")).toBeNull();
  });

  it("shows the reason codes of a denied run and no counts", () => {
    given({ state: "DENIED", denial_codes: ["SPONSOR_NOT_ACTIVE", "SOME_NEW_CODE"], summary: null, worker_attestation_id: null });
    expect(screen.getAllByText("Denied by policy").length).toBeGreaterThan(0);
    expect(screen.getByText("The agent's sponsor is not active.")).toBeTruthy();
    expect(screen.getByText("SPONSOR_NOT_ACTIVE")).toBeTruthy();
    expect(screen.getAllByText("SOME_NEW_CODE").length).toBeGreaterThan(0); // an unknown code is shown as itself, never hidden
    expect(screen.queryByText(/permitted records/)).toBeNull();
  });

  it("shows Integrity exception and nothing green when the evidence chain is broken", async () => {
    given({}, query({ data: { verified: false, integrity_exceptions: ["SEQ_GAP:3"], items: evidenceOk.items } }));
    expect(screen.getAllByText("Proof missing").length).toBeGreaterThan(0);
    expect(screen.queryByText("Done")).toBeNull();
    await userEvent.setup().click(screen.getByRole("tab", { name: "Evidence" }));
    expect(screen.getAllByText("Integrity exception").length).toBe(2); // the banner and the context panel
    expect(screen.getByText("SEQ_GAP:3")).toBeTruthy();
    expect(screen.queryByText("Evidence chain verified")).toBeNull();
    expect(screen.queryByText("Verified")).toBeNull();
    expect(screen.getByText("Integrity exception", { selector: "dd" })).toBeTruthy();
  });

  it("offers Retry only on a failed run", async () => {
    given({ state: "FAILED" });
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry run" }));
    expect(state.retry).toHaveBeenCalledTimes(1);
    cleanup();
    given({ state: "COMPLETED" });
    expect(screen.queryByRole("button", { name: "Retry run" })).toBeNull();
    cleanup();
    given({ state: "DENIED", denial_codes: ["SETUP_PAUSED"] });
    expect(screen.queryByRole("button", { name: "Retry run" })).toBeNull();
  });

  it("asks before pausing", async () => {
    given();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Pause future runs" }));
    expect(state.pause).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Pause agent" }));
    expect(state.pause).toHaveBeenCalledWith("OPERATOR_DECISION");
  });

  it("exports permitted evidence from the server only after a purpose is stated", async () => {
    const create = vi.fn(() => "blob:x");
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    given();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Export permitted evidence" }));
    const dialog = screen.getByRole("dialog");
    const go = within(dialog).getByRole("button", { name: "Export" });
    expect(go.hasAttribute("disabled") || go.getAttribute("aria-disabled") === "true").toBe(true);
    await user.type(within(dialog).getByRole("textbox", { name: /Purpose of the export/ }), "Quarterly audit Q4");
    await user.click(within(dialog).getByRole("button", { name: "Export" }));
    expect(state.exported).toHaveBeenCalledWith("Quarterly audit Q4");
    expect(create).toHaveBeenCalledTimes(1); // the server's answer is what is saved, not the page's own copy
    expect(click).toHaveBeenCalledTimes(1);
    click.mockRestore();
  });

  it("shows no explanation when there is none, and says the model was not used", () => {
    given();
    expect(screen.queryByText("AI explanation")).toBeNull();
    expect(screen.getByText("No. AI explanation is off.")).toBeTruthy();
  });

  it("labels a model explanation as not changing the result", () => {
    given({
      explanation: {
        source: "MODEL",
        text: "The run checked 12 records and flagged 2 findings.",
        fallback_reason: null,
        provider: "fake",
        model: "model-x",
        prompt_version: "daara-explain/1",
        facts_digest: "sha256:f",
        authoritative: false,
      },
    });
    expect(screen.getByText("AI explanation")).toBeTruthy();
    expect(screen.getByText(/does not change the result above, which is authoritative/)).toBeTruthy();
    expect(screen.getByText("The run checked 12 records and flagged 2 findings.")).toBeTruthy();
    expect(screen.getByText("Yes, for the explanation only (model-x)")).toBeTruthy();
    expect(screen.getByText(/Checked 12 permitted records/)).toBeTruthy(); // the rule result is still shown, first
  });

  it("labels the fixed fallback text with its reason", () => {
    given({
      explanation: {
        source: "TEMPLATE",
        text: "This run checked 12 permitted records.",
        fallback_reason: "UNSUPPORTED_CLAIM",
        provider: null,
        model: null,
        prompt_version: "daara-explain/1",
        facts_digest: "sha256:f",
        authoritative: false,
      },
    });
    expect(screen.getByText("Explanation (fixed text)")).toBeTruthy();
    expect(screen.getByText(/the model's text did not match the result/)).toBeTruthy();
    expect(screen.getByText("No. A fixed explanation was used.")).toBeTruthy();
  });

  it("exposes landmarks and a single page heading", () => {
    given();
    expect(screen.getByRole("complementary", { name: "Run context" })).toBeTruthy();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("tablist", { name: "Run details" })).toBeTruthy();
  });
});
