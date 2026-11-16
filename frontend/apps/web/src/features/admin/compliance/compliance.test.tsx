/** The compliance cases console (DTV-31, CR-101): one page for the company's own work waiting for a person, each source under its own permission. */
import { ApiError, type SofQueueItem, type WfRunSummary } from "@zoiko/api-client";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ queries: {} as Record<string, unknown> }));

vi.mock("@/lib/cell", () => ({
  useCellQuery: (key: string[]) => state.queries[key.at(-1) as string] ?? { isPending: true, fetchStatus: "fetching" },
  ApiErrorView: ({ error }: { error: Error }) => <div role="alert">api error: {error.message}</div>,
}));
vi.mock("next/link", () => ({ default: ({ href, children, className }: { href: string; children: ReactNode; className?: string }) => <a href={href} className={className}>{children}</a> }));

import { CompliancePage, runWaitText } from "./compliance-page";

afterEach(cleanup);
beforeEach(() => {
  state.queries = {};
});

const err = (status: number, code: string) => new ApiError({ status, code, message: code });
const query = (over: object) => ({ isPending: false, isError: false, fetchStatus: "idle", data: undefined, refetch: vi.fn(), ...over }) as never;
const sof = { case_id: "abcdef1234567890", subject_principal_id: "s1", country: "GB", questionnaire_version: "v", state: "NEEDS_REVIEW", submitted_at: "2026-11-16T10:00:00Z", valid_until: null } as SofQueueItem;
const run = { run_id: "99999999-aaaa-bbbb-cccc-000000000000", workflow_id: "onboarding", version: "1.0.0", subject_principal_id: "s2", status: "WAITING_FOR_REVIEW", outcome: null, awaiting_check: null, awaiting_role: "verification_reviewer", created_at: "2026-11-16T11:00:00Z", updated_at: "2026-11-16T11:00:00Z" } as WfRunSummary;

describe("the compliance console", () => {
  it("lists both kinds of work as links to where a person decides, and decides nothing itself", () => {
    state.queries.sof = query({ data: { items: [sof], notice: "n" } });
    state.queries.runs = query({ data: { items: [run], notice: "n" } });
    render(<CompliancePage />);
    expect(screen.getByRole("link", { name: "abcdef12" }).getAttribute("href")).toBe("/admin/source-of-funds/abcdef1234567890");
    expect(screen.getByRole("link", { name: "99999999" }).getAttribute("href")).toBe("/admin/workflows/onboarding/1.0.0");
    expect(screen.getByText("Waiting for a verification reviewer")).toBeTruthy();
    expect(screen.getByText("A person decides")).toBeTruthy();
    expect(screen.queryAllByRole("button", { name: /approve|clear|decline|decide|reject/i })).toEqual([]); // nothing here approves, clears or declines
  });

  it("says plainly when nothing is waiting", () => {
    state.queries.sof = query({ data: { items: [], notice: "n" } });
    state.queries.runs = query({ data: { items: [], notice: "n" } });
    render(<CompliancePage />);
    expect(screen.getByText("No source of funds cases are waiting.")).toBeTruthy();
    expect(screen.getByText("No workflow runs are waiting.")).toBeTruthy();
  });

  it("shows Withheld, not none, for the source a role may not read, and still shows the other", () => {
    state.queries.sof = query({ isError: true, error: err(403, "protected_action_denied") });
    state.queries.runs = query({ data: { items: [run], notice: "n" } });
    render(<CompliancePage />);
    expect(screen.getAllByText("Withheld for your role").length).toBe(1);
    expect(screen.queryByText("No source of funds cases are waiting.")).toBeNull();
    expect(screen.getByRole("link", { name: "99999999" })).toBeTruthy();
  });

  it("says Withheld for the runs source in its own words, and Not available when a line is switched off", () => {
    state.queries.sof = query({ isError: true, error: err(409, "service_unavailable") });
    state.queries.runs = query({ isError: true, error: err(403, "protected_action_denied") });
    render(<CompliancePage />);
    expect(screen.getByText("Not available")).toBeTruthy();
    expect(screen.getByText(/does not include reading verification workflow runs/)).toBeTruthy();
  });

  it("words a run's wait, and a run waiting for a check is not waiting for a person", () => {
    expect(runWaitText({ status: "WAITING_FOR_REVIEW", awaiting_role: "verification_reviewer" })).toBe("Waiting for a verification reviewer");
    expect(runWaitText({ status: "WAITING_FOR_REVIEW", awaiting_role: null })).toBe("Waiting for a reviewer");
    expect(runWaitText({ status: "WAITING_FOR_CHECK", awaiting_role: null })).toBe("Not waiting for a person");
  });
});
