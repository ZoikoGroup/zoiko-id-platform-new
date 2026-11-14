/** Readiness home (WF-AG-01): setups, runs and proposals listed; the feature being off, an unsupported region and a forbidden role are each their own state. */
import { ApiError, type ReadinessProposal, type ReadinessSetup } from "@zoiko/api-client";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ queries: {} as Record<string, unknown> }));

vi.mock("@/lib/cell", () => ({
  useCellQuery: (key: string[]) => state.queries[key.includes("proposals") ? "proposals" : key.includes("runs") ? "runs" : "setups"] ?? { isPending: true, fetchStatus: "fetching" },
  ApiErrorView: ({ error }: { error: Error }) => <div role="alert">api error: {error.message}</div>,
}));
vi.mock("next/link", () => ({ default: ({ href, children, className }: { href: string; children: ReactNode; className?: string }) => <a href={href} className={className}>{children}</a> }));

import { ReadinessHomePage } from "./readiness-home";

afterEach(cleanup);
beforeEach(() => {
  state.queries = {};
});

const err = (status: number, code: string) => new ApiError({ status, code, message: code });
const query = (over: object) => ({ isPending: false, isError: false, fetchStatus: "idle", data: undefined, refetch: vi.fn(), ...over }) as never;

const setup = { setup_id: "s1", agent_id: "agent-1234567890", display_name: "Access Readiness", status: "APPROVED", scope: { home_region: "eu" } } as unknown as ReadinessSetup;
const proposal = { proposal_id: "p1", rule_id: "R-01", finding_code: "EXPIRED_MANDATE", target: "mandate:1234abcd...", severity: "HIGH", status: "OPEN" } as unknown as ReadinessProposal;
const runs = { items: [{ run_id: "abcdef123456", state: "COMPLETED", created_at: "2026-11-14T10:00:00Z" }] };

describe("readiness home", () => {
  it("lists setups with their runs, and proposals", () => {
    state.queries.setups = query({ data: { items: [setup] } });
    state.queries.runs = query({ data: runs });
    state.queries.proposals = query({ data: { items: [proposal] } });
    render(<ReadinessHomePage />);
    expect(screen.getByText("Access Readiness")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open setup" }).getAttribute("href")).toBe("/admin/agents/readiness/agent-1234567890/setup");
    expect(screen.getByRole("link", { name: "Run abcdef12" }).getAttribute("href")).toBe("/admin/agents/readiness/agent-1234567890/runs/abcdef123456");
    expect(screen.getByRole("link", { name: /R-01 Expired mandate/ }).getAttribute("href")).toBe("/admin/agent-review/p1");
    expect(screen.getByText("Severity: HIGH")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Create agent" })).toBeTruthy();
  });

  it("says plainly when nothing exists yet, which is not the same as being unavailable", () => {
    state.queries.setups = query({ data: { items: [] } });
    state.queries.proposals = query({ data: { items: [] } });
    render(<ReadinessHomePage />);
    expect(screen.getByText("No access readiness agent set up yet")).toBeTruthy();
    expect(screen.getByText("No proposals yet.")).toBeTruthy();
    expect(screen.queryByText("Not available")).toBeNull();
  });

  it("says Not available, never an empty list, when the feature is off", () => {
    state.queries.setups = query({ isError: true, error: err(404, "NOT_FOUND") });
    state.queries.proposals = query({ isError: true, error: err(404, "NOT_FOUND") });
    render(<ReadinessHomePage />);
    expect(screen.getAllByText("Not available").length).toBe(2);
    expect(screen.queryByText("No access readiness agent set up yet")).toBeNull();
    expect(screen.queryByText("No proposals yet.")).toBeNull();
  });

  it("says Not available in this region for an unsupported region", () => {
    state.queries.setups = query({ isError: true, error: err(422, "REGION_UNSUPPORTED") });
    state.queries.proposals = query({ data: { items: [] } });
    render(<ReadinessHomePage />);
    expect(screen.getByText("Not available in this region")).toBeTruthy();
    expect(screen.queryByText("No access readiness agent set up yet")).toBeNull();
  });

  it("shows a redacted state, not an empty one, to a role that may not see it", () => {
    state.queries.setups = query({ isError: true, error: err(403, "PROTECTED_ACTION_DENIED") });
    state.queries.proposals = query({ isError: true, error: err(403, "PROTECTED_ACTION_DENIED") });
    render(<ReadinessHomePage />);
    expect(screen.getAllByText("Redacted for your role").length).toBe(2);
    expect(screen.queryByText("No proposals yet.")).toBeNull();
  });

  it("has one page heading, and every link is a real link with a name", () => {
    state.queries.setups = query({ data: { items: [setup] } });
    state.queries.runs = query({ data: runs });
    state.queries.proposals = query({ data: { items: [proposal] } });
    render(<ReadinessHomePage />);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    for (const a of screen.getAllByRole("link")) expect((a.textContent ?? "").trim().length).toBeGreaterThan(0);
  });
});
