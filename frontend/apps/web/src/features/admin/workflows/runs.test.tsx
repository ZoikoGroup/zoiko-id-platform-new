/** Workflow run screens (DTV-30, CR-099): the list, what a run waits for, recording a result, and the rule that nobody reviews their own run. */
import { ApiError, type WfRun, type WfRunSummary } from "@zoiko/api-client";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ queries: {} as Record<string, unknown>, calls: [] as { name: string; arg: unknown }[] }));

vi.mock("@/lib/cell", () => ({
  useCellQuery: (key: string[]) => state.queries[key.includes("runs") ? "list" : "run"] ?? { isPending: true, fetchStatus: "fetching" },
  useCellMutation: (fn: { toString(): string }) => {
    const src = String(fn);
    const name = ["reportCheck", "completeReview"].find((n) => src.includes(`workflows.${n}(`)) ?? "other";
    return { mutate: (arg: unknown) => state.calls.push({ name, arg }), isPending: false, error: null, data: undefined };
  },
  ApiErrorView: ({ error }: { error: Error }) => <div role="alert">api error: {error.message}</div>,
}));
vi.mock("../authority/shared", () => ({ useMe: () => undefined }));
vi.mock("next/link", () => ({ default: ({ href, children, className }: { href: string; children: ReactNode; className?: string }) => <a href={href} className={className}>{children}</a> }));

import { WorkflowRunPage, WorkflowRunsPage, canReview, waitingText } from "./runs";

afterEach(cleanup);
beforeEach(() => {
  state.queries = {};
  state.calls = [];
});

const query = (over: object) => ({ isPending: false, isError: false, fetchStatus: "idle", data: undefined, refetch: vi.fn(), ...over }) as never;
const err = (status: number, code: string) => new ApiError({ status, code, message: code });
const summary = { run_id: "abcdef1234567890", workflow_id: "onboarding", version: "1.0.0", subject_principal_id: "subject-1", status: "WAITING_FOR_REVIEW", outcome: null, awaiting_check: null, awaiting_role: "verification_reviewer", created_at: "2026-11-16T10:00:00Z", updated_at: "2026-11-16T10:00:00Z" } as WfRunSummary;
const run = (over: Partial<WfRun> = {}) =>
  ({ ...summary, content_hash: "h", step: "look", results: { IDENTITY_DOCUMENT: "VERIFIED", LIVENESS: "NOT_VERIFIED" }, events: [{ step: "identity", kind: "ENTERED", detail: "" }, { step: "liveness", kind: "CHECK_RESULT", detail: "LIVENESS NOT_VERIFIED" }], notifications: [], erased: false, notice: "n", ...over }) as WfRun;

describe("words and rules", () => {
  it("says what a run waits for and never offers review to the applicant or to nobody", () => {
    expect(waitingText({ status: "WAITING_FOR_CHECK", awaiting_check: "AML_SCREENING", awaiting_role: null })).toBe("The aml screening result");
    expect(waitingText({ status: "WAITING_FOR_REVIEW", awaiting_check: null, awaiting_role: "verification_reviewer" })).toBe("A verification reviewer to look");
    expect(waitingText({ status: "ENDED", awaiting_check: null, awaiting_role: null })).toBe("Nothing");
    expect(canReview({ status: "WAITING_FOR_REVIEW", subject_principal_id: "a" }, "b")).toBe(true);
    expect(canReview({ status: "WAITING_FOR_REVIEW", subject_principal_id: "a" }, "a")).toBe(false);
    expect(canReview({ status: "WAITING_FOR_REVIEW", subject_principal_id: "a" }, undefined)).toBe(false);
    expect(canReview({ status: "WAITING_FOR_CHECK", subject_principal_id: "a" }, "b")).toBe(false);
  });
});

describe("the runs list", () => {
  it("lists runs as links and says Withheld, not none, to a role that may not read", () => {
    state.queries.list = query({ data: { items: [summary], notice: "n" } });
    const { unmount } = render(<WorkflowRunsPage />);
    expect(screen.getByRole("link", { name: "abcdef12" }).getAttribute("href")).toBe("/admin/workflows/runs/abcdef1234567890");
    expect(screen.getByText("Waiting for a person")).toBeTruthy();
    unmount();
    state.queries.list = query({ isError: true, error: err(403, "protected_action_denied") });
    render(<WorkflowRunsPage />);
    expect(screen.getByText("Withheld for your role")).toBeTruthy();
    expect(screen.queryByText("No runs yet.")).toBeNull();
  });
});

describe("a run", () => {
  it("shows the results as words and the trace, and a reviewer other than the applicant may record a review", () => {
    state.queries.run = query({ data: run() });
    const { unmount } = render(<WorkflowRunPage runId="abcdef1234567890" me="reviewer-1" />);
    expect(screen.getByText("liveness: not verified")).toBeTruthy();
    expect(screen.getByText(/liveness: check result \(LIVENESS NOT_VERIFIED\)/)).toBeTruthy();
    const button = screen.getByRole("button", { name: "Record that I reviewed this" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    expect(state.calls.at(-1)?.name).toBe("completeReview");
    unmount();
    render(<WorkflowRunPage runId="abcdef1234567890" me="subject-1" />);
    expect((screen.getByRole("button", { name: "Record that I reviewed this" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/This run is about you/)).toBeTruthy();
  });

  it("records the awaited check with the chosen outcome and offers nothing else", () => {
    state.queries.run = query({ data: run({ status: "WAITING_FOR_CHECK", awaiting_check: "AML_SCREENING", awaiting_role: null, results: {}, events: [] }) });
    render(<WorkflowRunPage runId="abcdef1234567890" me="x" />);
    fireEvent.click(screen.getByRole("button", { name: "Record result" }));
    expect(state.calls.at(-1)).toEqual({ name: "reportCheck", arg: { check: "AML_SCREENING", outcome: "VERIFIED" } });
    expect(screen.queryByRole("button", { name: "Record that I reviewed this" })).toBeNull();
    expect(screen.getByText("No results yet.")).toBeTruthy();
  });

  it("shows an ended run with its outcome and no controls, and says when results were erased", () => {
    state.queries.run = query({ data: run({ status: "ENDED", outcome: "REFER_TO_PERSON", awaiting_role: null, erased: true, results: {} }) });
    render(<WorkflowRunPage runId="abcdef1234567890" me="x" />);
    expect(screen.getByText(/Ended: refer to person\./)).toBeTruthy();
    expect(screen.getByText(/erased under a data-rights request/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Record result" })).toBeNull();
  });
});
