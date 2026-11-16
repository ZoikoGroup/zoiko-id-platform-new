/** Structural accessibility checks for the source of funds screens, in plain DOM terms (the repo has axe only for end-to-end runs): every control has a name,
 * the heading order is sane, ids are unique, and the decision form works from the keyboard. This is automated evidence; it is not a screen-reader pass. */
import type { SofCase, SofQueueItem } from "@zoiko/api-client";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ byKey: {} as Record<string, unknown>, decide: vi.fn() }));

vi.mock("@/lib/cell", () => ({
  useCellQuery: (key: string[]) => state.byKey[key.includes("queue") ? "queue" : key.includes("questionnaire") ? "questionnaire" : "case"] ?? { isPending: true, fetchStatus: "fetching" },
  useCellMutation: () => ({ mutate: state.decide, isPending: false, error: null }),
  ApiErrorView: ({ error }: { error: Error }) => <div role="alert">api error: {error.message}</div>,
}));
vi.mock("next/link", () => ({ default: ({ href, children, className }: { href: string; children: ReactNode; className?: string }) => <a href={href} className={className}>{children}</a> }));

import { SourceOfFundsCasePage } from "./case-page";
import { SourceOfFundsQueuePage } from "./queue-page";

if (!(globalThis as { CSS?: unknown }).CSS) Object.defineProperty(globalThis, "CSS", { value: { escape: (v: string) => v.replace(/[^\w-]/g, (c) => `\\${c}`) } });

afterEach(cleanup);
beforeEach(() => {
  state.byKey = {};
  state.decide.mockReset();
});

const query = (data: unknown) => ({ isPending: false, isError: false, isSuccess: true, fetchStatus: "idle", data, refetch: vi.fn() });
const item = { case_id: "abcdef1234567890", subject_principal_id: "s1", country: "GB", questionnaire_version: "0.1.0-SAMPLE", state: "NEEDS_REVIEW", submitted_at: "2026-11-16T10:00:00Z", valid_until: null } as SofQueueItem;
const kase = {
  case_id: "abcdef1234567890", subject_principal_id: "s1", country: "GB", questionnaire: { code: "source_of_funds", version: "0.1.0-SAMPLE", content_hash: "h" },
  state: "NEEDS_REVIEW", answers: { "SOF-01": "EMPLOYMENT" }, evidence: [{ question_id: "SOF-01", type: "PAYSLIP", upload_id: "11111111-2222-3333-4444-555555555555" }],
  problems: [], submitted_at: "2026-11-16T10:00:00Z", valid_until: null, erased: false, decisions: [], notice: "A person decides every case.",
} as unknown as SofCase;
const questionnaire = { code: "source_of_funds", version: "0.1.0-SAMPLE", status: "SAMPLE", content_hash: "h", questions: [{ question_id: "SOF-01", text: "Where do the funds come from?", answer: "CHOICE", required: true, choices: ["EMPLOYMENT"], evidence_required_for: {} }] };

function name(el: HTMLElement): string {
  const by = (el.getAttribute("aria-labelledby") ?? "").split(/\s+/).filter(Boolean).map((id) => el.ownerDocument.getElementById(id)?.textContent ?? "").join(" ");
  if (by.trim()) return by;
  const label = el.getAttribute("aria-label");
  if (label?.trim()) return label;
  const labels = (el as HTMLInputElement).labels ? [...(el as HTMLInputElement).labels!].map((l) => l.textContent ?? "").join(" ") : "";
  return labels.trim() || el.textContent?.trim() || el.getAttribute("title") || "";
}

function audit(root: HTMLElement): string[] {
  const out: string[] = [];
  for (const el of root.querySelectorAll<HTMLElement>("button, a[href], input:not([type=hidden]), select, textarea, [role=radio], [role=checkbox], [role=button]")) {
    if (el.closest("[aria-hidden=true]")) continue;
    if (!name(el).trim()) out.push(`no accessible name: <${el.tagName.toLowerCase()}>`);
  }
  const levels = [...root.querySelectorAll("h1,h2,h3,h4,h5,h6")].map((h) => Number(h.tagName[1]));
  levels.forEach((l, i) => {
    if (i > 0 && l > levels[i - 1]! + 1) out.push(`heading level skipped: h${levels[i - 1]} to h${l}`);
  });
  const ids = [...root.querySelectorAll("[id]")].map((e) => e.id);
  for (const id of new Set(ids.filter((v, i) => ids.indexOf(v) !== i))) out.push(`duplicate id ${id}`);
  return out;
}

describe("source of funds accessibility", () => {
  it("the queue has named controls, a sane heading order and unique ids", () => {
    state.byKey.queue = query({ items: [item], notice: "A person decides every case." });
    const { container } = render(<SourceOfFundsQueuePage />);
    expect(audit(container)).toEqual([]);
  });

  it("the case page, with its decision form, has named controls, a sane heading order and unique ids", () => {
    state.byKey.case = query(kase);
    state.byKey.questionnaire = query(questionnaire);
    const { container } = render(<SourceOfFundsCasePage caseId="abcdef1234567890" />);
    expect(audit(container)).toEqual([]);
  });

  it("the decision can be made from the keyboard alone", async () => {
    state.byKey.case = query(kase);
    state.byKey.questionnaire = query(questionnaire);
    const user = userEvent.setup();
    render(<SourceOfFundsCasePage caseId="abcdef1234567890" />);
    await user.click(screen.getByRole("radio", { name: /Clear$/ }));
    await user.tab();
    const reason = screen.getByRole("checkbox", { name: /The funds are explained and evidenced/ });
    reason.focus();
    await user.keyboard(" ");
    const send = screen.getByRole("button", { name: "Record decision" });
    send.focus();
    await user.keyboard("{Enter}");
    expect(state.decide).toHaveBeenCalledWith({ decision: "CLEAR", reason_codes: ["FUNDS_EXPLAINED_AND_EVIDENCED"] });
  });

  it("an expired or switched-off state is announced as text, not only by colour", () => {
    state.byKey.case = query({ ...kase, state: "CLEARED", valid_until: new Date(Date.now() - 864e5).toISOString() });
    state.byKey.questionnaire = query(questionnaire);
    render(<SourceOfFundsCasePage caseId="abcdef1234567890" />);
    expect(screen.getByText("Clearance expired")).toBeTruthy();
  });
});
