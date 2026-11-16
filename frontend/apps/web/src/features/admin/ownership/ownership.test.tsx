/** Ownership chain screens (DTV-24, CR-102): candidates for a person to confirm, gaps that are not zeros, and a graph refused in words. */
import { ApiError, type OwnFindings, type OwnGraph, type OwnGraphSummary } from "@zoiko/api-client";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ queries: {} as Record<string, unknown>, calls: [] as { name: string; arg: unknown }[], data: {} as Record<string, unknown> }));

vi.mock("@/lib/cell", () => ({
  useCellQuery: (key: string[]) => state.queries[key.includes("list") ? "list" : "graph"] ?? { isPending: true, fetchStatus: "fetching" },
  useCellMutation: (fn: { toString(): string }, opts?: { onSuccess?: (d: unknown) => void }) => {
    const name = String(fn).includes("ownership.analyze(") ? "analyze" : "record";
    return {
      mutate: (arg: unknown) => {
        state.calls.push({ name, arg });
        if (state.data[name] !== undefined) opts?.onSuccess?.(state.data[name]);
      },
      isPending: false,
      error: null,
      data: undefined,
    };
  },
  ApiErrorView: ({ error }: { error: Error }) => <div role="alert">api error: {error.message}</div>,
}));
vi.mock("next/link", () => ({ default: ({ href, children, className }: { href: string; children: ReactNode; className?: string }) => <a href={href} className={className}>{children}</a> }));

import { FindingsView, NewOwnershipPage, OwnershipDetailPage, OwnershipListPage, STARTER, parseGraph, problemText } from "./ownership";

afterEach(cleanup);
beforeEach(() => {
  state.queries = {};
  state.calls = [];
  state.data = {};
});

const query = (over: object) => ({ isPending: false, isError: false, fetchStatus: "idle", data: undefined, refetch: vi.fn(), ...over }) as never;
const err = (status: number, code: string) => new ApiError({ status, code, message: code });
const findings = {
  target: "target-co", threshold: "25", complete: false, recommendation: "REVIEW", fallback: null, recognised_ends: [],
  candidates: [
    { entity_id: "person-1", effective_percent: "30", reasons: ["OWNERSHIP_AT_OR_ABOVE_THRESHOLD"], paths: 1, status: "NEEDS_EVIDENCE" },
    { entity_id: "person-2", effective_percent: "30", reasons: ["OWNERSHIP_AT_OR_ABOVE_THRESHOLD", "CONTROL_BY_OTHER_MEANS"], paths: 2, status: "NEEDS_CONFIRMATION" },
  ],
  gaps: [{ code: "UNRESOLVED_CHAIN", entity_id: "holdco2", effective_percent: "40" }],
  notes: ["These are candidates for a person to confirm. Nothing here says anyone IS a beneficial owner."],
} as OwnFindings;

describe("words", () => {
  it("says a refusal in words and shows an unknown code as itself", () => {
    expect(problemText("ownership_cycle")).toMatch(/loops back/);
    expect(problemText("OWNED_OVER_100_PERCENT")).toMatch(/more than 100 percent/);
    expect(problemText("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });
  it("refuses anything that is not one JSON object before it is sent", () => {
    expect(parseGraph("{").ok).toBe(false);
    expect(parseGraph("[]").ok).toBe(false);
    expect(parseGraph("null").ok).toBe(false);
    expect(parseGraph(STARTER).ok).toBe(true);
  });
});

describe("the findings", () => {
  it("shows candidates that need a person and a gap that is not a zero, and never says anyone is an owner", () => {
    render(<FindingsView findings={findings} />);
    expect(screen.getByText("There is a gap a person must close")).toBeTruthy();
    expect(screen.getByText("Needs evidence")).toBeTruthy();
    expect(screen.getByText("Needs confirmation")).toBeTruthy();
    expect(screen.getByText(/no owners recorded, up to 40 percent/)).toBeTruthy();
    expect(screen.getByText(/control by other means/)).toBeTruthy();
    expect(screen.getByText(/Nothing here says anyone IS a beneficial owner/)).toBeTruthy();
  });
  it("says plainly when there is no candidate and no gap", () => {
    render(<FindingsView findings={{ ...findings, complete: true, candidates: [], gaps: [] }} />);
    expect(screen.getByText("No material gap")).toBeTruthy();
    expect(screen.getByText(/No candidate at this threshold/)).toBeTruthy();
    expect(screen.getByText("No gaps.")).toBeTruthy();
  });
});

describe("the pages", () => {
  it("lists versions as links and says Withheld, not none, to a role that may not read", () => {
    state.queries.list = query({ data: { items: [{ graph_id: "g-1", target: "target-co", version: 2, content_hash: "a".repeat(64), author: "u", created_at: "2026-11-16T10:00:00Z" } as OwnGraphSummary] } });
    const { unmount } = render(<OwnershipListPage />);
    expect(screen.getByRole("link", { name: "target-co" }).getAttribute("href")).toBe("/admin/ownership/g-1");
    unmount();
    state.queries.list = query({ isError: true, error: err(403, "protected_action_denied") });
    render(<OwnershipListPage />);
    expect(screen.getByText("Withheld for your role")).toBeTruthy();
    expect(screen.queryByText("No ownership graphs yet.")).toBeNull();
  });

  it("does not send what is not a JSON object, and sends the starter graph to be worked out", () => {
    state.data.analyze = findings;
    render(<NewOwnershipPage />);
    fireEvent.change(screen.getByLabelText("Graph"), { target: { value: "{ not json" } });
    fireEvent.click(screen.getByRole("button", { name: "Work out candidates" }));
    expect(screen.getByText(/not valid JSON/)).toBeTruthy();
    expect(state.calls).toEqual([]);
    fireEvent.change(screen.getByLabelText("Graph"), { target: { value: STARTER } });
    fireEvent.click(screen.getByRole("button", { name: "Work out candidates" }));
    expect(state.calls.map((c) => c.name)).toEqual(["analyze"]);
    expect(screen.getByText("There is a gap a person must close")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Record version" }));
    expect(state.calls.map((c) => c.name)).toEqual(["analyze", "record"]);
  });

  it("shows a recorded version with its findings, or says why they are not available", () => {
    state.queries.graph = query({ data: { graph_id: "g-1", target: "target-co", version: 2, threshold: "25", content_hash: "h", author: "u", created_at: "x", entities: [], links: [], findings } as OwnGraph });
    const { unmount } = render(<OwnershipDetailPage graphId="g-1" />);
    expect(screen.getByText("Candidates")).toBeTruthy();
    unmount();
    state.queries.graph = query({ data: { graph_id: "g-1", target: "target-co", version: 2, threshold: "25", content_hash: "h", author: "u", created_at: "x", entities: [], links: [], findings: null, findings_error: "OWNERSHIP_CYCLE" } as OwnGraph });
    render(<OwnershipDetailPage graphId="g-1" />);
    expect(screen.getByText("Findings are not available")).toBeTruthy();
    expect(screen.getByText(/loops back/)).toBeTruthy();
  });
});
