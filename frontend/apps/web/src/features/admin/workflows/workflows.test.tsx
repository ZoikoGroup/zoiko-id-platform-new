/** Verification workflow screens (DTV-30, CR-099): the safety rules in words, the editor, the simulator and the maker-checker approval. */
import { ApiError, type WfCheckResult, type WfVersion, type WfVersionSummary } from "@zoiko/api-client";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ queries: {} as Record<string, unknown>, calls: [] as { name: string; arg: unknown }[], mutationData: {} as Record<string, unknown> }));

vi.mock("@/lib/cell", () => ({
  useCellQuery: (key: string[]) => state.queries[key.includes("list") ? "list" : "version"] ?? { isPending: true, fetchStatus: "fetching" },
  useCellMutation: (fn: { toString(): string }, opts?: { onSuccess?: (d: unknown) => void }) => {
    const src = String(fn);
    const name = ["check", "create", "simulate", "approve", "retire"].find((n) => src.includes(`workflows.${n}(`)) ?? "other";
    return {
      mutate: (arg: unknown) => {
        state.calls.push({ name, arg });
        if (state.mutationData[name] !== undefined) opts?.onSuccess?.(state.mutationData[name]);
      },
      isPending: false,
      error: null,
      data: undefined,
    };
  },
  ApiErrorView: ({ error }: { error: Error }) => <div role="alert">api error: {error.message}</div>,
}));
vi.mock("next/link", () => ({ default: ({ href, children, className }: { href: string; children: ReactNode; className?: string }) => <a href={href} className={className}>{children}</a> }));

import { canApprove, classify, parseDefinition, problemText, requiredChecks, STARTER } from "./logic";
import { NewWorkflowPage, WorkflowListPage, WorkflowVersionPage } from "./workflows";

afterEach(cleanup);
beforeEach(() => {
  state.queries = {};
  state.calls = [];
  state.mutationData = {};
});

const query = (over: object) => ({ isPending: false, isError: false, fetchStatus: "idle", data: undefined, refetch: vi.fn(), ...over }) as never;
const err = (status: number, code: string) => new ApiError({ status, code, message: code });
const summary = { workflow_id: "onboarding", version: "1.0.0", status: "DRAFT", content_hash: "a".repeat(64), author: "me-1", approved_by: null, created_at: "2026-11-16T10:00:00Z" } as WfVersionSummary;
const version = {
  workflow_id: "onboarding", version: "1.0.0", status: "DRAFT", content_hash: "b".repeat(64), author: "author-1", approved_by: null, approved_at: null, problems: [],
  definition: { id: "onboarding", required_checks: ["IDENTITY_DOCUMENT", "LIVENESS"] }, notice: "n",
} as unknown as WfVersion;

describe("logic", () => {
  it("says each safety problem in words and shows an unknown code as itself", () => {
    expect(problemText({ code: "APPROVAL_NOT_GUARDED", where: "identity > approved" })).toMatch(/Approved without every required check/);
    expect(problemText({ code: "CYCLE", where: "" })).toMatch(/loop/);
    expect(problemText({ code: "SOMETHING_NEW", where: "x" })).toBe("SOMETHING_NEW (x)");
  });

  it("reads the required checks from a definition and nothing else", () => {
    expect(requiredChecks({ required_checks: ["A", "B", 3] })).toEqual(["A", "B"]);
    expect(requiredChecks({ steps: {} })).toEqual([]);
    expect(requiredChecks(undefined)).toEqual([]);
  });

  it("lets a person approve only a clean draft they did not write", () => {
    expect(canApprove("DRAFT", "a", "b", [])).toBe(true);
    expect(canApprove("DRAFT", "a", "a", [])).toBe(false); // the author
    expect(canApprove("DRAFT", "a", "b", [{ code: "CYCLE", where: "" }])).toBe(false);
    expect(canApprove("DRAFT", "a", undefined, [])).toBe(false);
    expect(canApprove("DRAFT", "a", "b", null)).toBe(true);
    expect(canApprove("APPROVED", "a", "b", [])).toBe(false);
    expect(canApprove("RETIRED", "a", "b", [])).toBe(false);
  });

  it("refuses anything that is not one JSON object before it is sent", () => {
    expect(parseDefinition("{").ok).toBe(false);
    expect(parseDefinition("[1]").ok).toBe(false);
    expect(parseDefinition("null").ok).toBe(false);
    expect(parseDefinition(STARTER).ok).toBe(true);
  });

  it("separates a forbidden role from a missing item", () => {
    expect(classify(err(403, "protected_action_denied"))).toBe("forbidden");
    expect(classify(err(404, "workflow_not_found"))).toBe("missing");
    expect(classify(err(500, "boom"))).toBe("other");
  });
});

describe("the list", () => {
  it("lists versions as links with their status and fingerprint", () => {
    state.queries.list = query({ data: { items: [summary], notice: "n" } });
    render(<WorkflowListPage />);
    expect(screen.getByRole("link", { name: "onboarding" }).getAttribute("href")).toBe("/admin/workflows/onboarding/1.0.0");
    expect(screen.getByText("Draft")).toBeTruthy();
    expect(screen.getByRole("link", { name: "New workflow" }).getAttribute("href")).toBe("/admin/workflows/new");
  });

  it("says plainly when there are none, and says Withheld, not none, to a role that may not read", () => {
    state.queries.list = query({ data: { items: [], notice: "n" } });
    const { unmount } = render(<WorkflowListPage />);
    expect(screen.getByText("No workflows yet.")).toBeTruthy();
    unmount();
    state.queries.list = query({ isError: true, error: err(403, "protected_action_denied") });
    render(<WorkflowListPage />);
    expect(screen.getByText("Withheld for your role")).toBeTruthy();
    expect(screen.queryByText("No workflows yet.")).toBeNull();
  });
});

describe("the editor", () => {
  it("does not send a definition that is not a JSON object", () => {
    render(<NewWorkflowPage />);
    fireEvent.change(screen.getByLabelText("Definition"), { target: { value: "{ not json" } });
    fireEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(screen.getByText(/not valid JSON/)).toBeTruthy();
    expect(state.calls).toEqual([]);
  });

  it("checks the starter definition without saving it and shows the answer in words", () => {
    state.mutationData.check = { valid: true, parse_error: null, detail: "", problems: [{ code: "APPROVAL_NOT_GUARDED", where: "x" }], content_hash: "c".repeat(64) } as WfCheckResult;
    render(<NewWorkflowPage />);
    fireEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(state.calls.map((c) => c.name)).toEqual(["check"]);
    expect(screen.getByText("This workflow does not pass the safety rules yet")).toBeTruthy();
    expect(screen.getByText(/Approved without every required check/)).toBeTruthy();
  });

  it("says a clean definition passes, and saving sends it as a draft", () => {
    state.mutationData.check = { valid: true, parse_error: null, detail: "", problems: [], content_hash: "c".repeat(64) } as WfCheckResult;
    render(<NewWorkflowPage />);
    fireEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(screen.getByText("Passes the safety rules")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    expect(state.calls.map((c) => c.name)).toEqual(["check", "create"]);
  });

  it("explains a definition that does not parse", () => {
    state.mutationData.check = { valid: false, parse_error: "DECISION_WITHOUT_ELSE", detail: "DECISION_WITHOUT_ELSE: decide", problems: [], content_hash: null } as WfCheckResult;
    render(<NewWorkflowPage />);
    fireEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(screen.getByText(/needs an else/)).toBeTruthy();
  });
});

describe("a version", () => {
  it("simulates with the chosen results and says where it ends, without storing anything", () => {
    state.queries.version = query({ data: version });
    state.mutationData.simulate = { steps: ["identity", "liveness", "decide", "look", "refer"], outcome: "REFER_TO_PERSON", needs_person: ["verification_reviewer"], side_effects: "none: a simulation stores and sends nothing" };
    render(<WorkflowVersionPage id="onboarding" version="1.0.0" me="approver-1" />);
    fireEvent.click(screen.getByRole("button", { name: "Simulate" }));
    expect(state.calls[0]).toEqual({ name: "simulate", arg: { IDENTITY_DOCUMENT: "VERIFIED", LIVENESS: "VERIFIED" } });
    expect(screen.getByText("Ends: refer to person")).toBeTruthy();
    expect(screen.getByText(/none: a simulation stores and sends nothing/)).toBeTruthy();
    expect(screen.getByText(/A person reviews \(verification_reviewer\)/)).toBeTruthy();
  });

  it("lets a different person approve a clean draft, and tells the author someone else must", () => {
    state.queries.version = query({ data: version });
    const { unmount } = render(<WorkflowVersionPage id="onboarding" version="1.0.0" me="approver-1" />);
    const approve = screen.getByRole("button", { name: "Approve this version" }) as HTMLButtonElement;
    expect(approve.disabled).toBe(false);
    fireEvent.click(approve);
    expect(state.calls.at(-1)?.name).toBe("approve");
    unmount();
    render(<WorkflowVersionPage id="onboarding" version="1.0.0" me="author-1" />);
    expect((screen.getByRole("button", { name: "Approve this version" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/You wrote this version, so someone else approves it/)).toBeTruthy();
  });

  it("will not offer approval while the workflow has safety problems, and shows them", () => {
    state.queries.version = query({ data: { ...version, problems: [{ code: "REGULATED_CHECK_AUTO_APPROVES", where: "AML_SCREENING: screen > approved" }] } });
    render(<WorkflowVersionPage id="onboarding" version="1.0.0" me="approver-1" />);
    expect((screen.getByRole("button", { name: "Approve this version" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/regulated check can lead to Approved with no person looking/)).toBeTruthy();
  });

  it("shows an approved version as frozen with no approve button, and offers to retire it", () => {
    state.queries.version = query({ data: { ...version, status: "APPROVED", approved_by: "approver-1-uuid" } });
    render(<WorkflowVersionPage id="onboarding" version="1.0.0" me="approver-1" />);
    expect(screen.getByText("Approved")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve this version" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retire this version" }));
    expect(state.calls.at(-1)?.name).toBe("retire");
  });

  it("says Withheld for your role when the role may not read it", () => {
    state.queries.version = query({ isError: true, error: err(403, "protected_action_denied") });
    render(<WorkflowVersionPage id="onboarding" version="1.0.0" />);
    expect(screen.getByText("Withheld for your role")).toBeTruthy();
  });
});
