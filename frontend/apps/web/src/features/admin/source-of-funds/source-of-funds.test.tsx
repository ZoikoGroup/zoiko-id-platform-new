/** Source of funds queue and case screens (DTV-28, CR-096): the switched-off line, the redacted role, and the decision form's rules. */
import { ApiError, type SofCase, type SofQueueItem } from "@zoiko/api-client";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ queries: {} as Record<string, unknown>, decide: vi.fn(), appeal: vi.fn() }));

vi.mock("@/lib/cell", () => ({
  useCellQuery: (key: string[]) => state.queries[key.includes("queue") ? "queue" : key.includes("questionnaire") ? "questionnaire" : "case"] ?? { isPending: true, fetchStatus: "fetching" },
  useCellMutation: (fn: { toString(): string }) => {
    const isAppeal = String(fn).includes("appeal");
    return { mutate: isAppeal ? state.appeal : state.decide, isPending: false, error: null };
  },
  ApiErrorView: ({ error }: { error: Error }) => <div role="alert">api error: {error.message}</div>,
}));
vi.mock("next/link", () => ({ default: ({ href, children, className }: { href: string; children: ReactNode; className?: string }) => <a href={href} className={className}>{children}</a> }));

import { SourceOfFundsCasePage } from "./case-page";
import { SourceOfFundsQueuePage } from "./queue-page";

afterEach(cleanup);
beforeEach(() => {
  state.queries = {};
  state.decide.mockClear();
  state.appeal.mockClear();
});

const err = (status: number, code: string) => new ApiError({ status, code, message: code });
const query = (over: object) => ({ isPending: false, isError: false, fetchStatus: "idle", data: undefined, refetch: vi.fn(), ...over }) as never;

const item = { case_id: "abcdef1234567890", subject_principal_id: "s1", country: "GB", questionnaire_version: "0.1.0-SAMPLE", state: "NEEDS_REVIEW", submitted_at: "2026-11-16T10:00:00Z", valid_until: null } as SofQueueItem;
const questionnaire = {
  code: "source_of_funds", version: "0.1.0-SAMPLE", status: "SAMPLE", content_hash: "h",
  questions: [
    { question_id: "SOF-01", text: "Where do the funds come from?", answer: "CHOICE", required: true, choices: ["EMPLOYMENT"], evidence_required_for: {} },
    { question_id: "SOF-02", text: "How much?", answer: "AMOUNT", required: true, choices: [], evidence_required_for: {} },
  ],
};
const baseCase = {
  case_id: "abcdef1234567890", subject_principal_id: "s1", country: "GB", questionnaire: { code: "source_of_funds", version: "0.1.0-SAMPLE", content_hash: "h" },
  state: "NEEDS_REVIEW", answers: { "SOF-01": "EMPLOYMENT", "SOF-02": { value: "40000.00", currency: "GBP" } },
  evidence: [{ question_id: "SOF-01", type: "PAYSLIP", upload_id: "11111111-2222-3333-4444-555555555555" }], problems: [],
  submitted_at: "2026-11-16T10:00:00Z", valid_until: null, erased: false, decisions: [], notice: "A person decides every case. This service never clears anyone by itself, and nothing here is a score.",
} as unknown as SofCase;

describe("the queue", () => {
  it("lists cases without answers, as links to the case", () => {
    state.queries.queue = query({ data: { items: [item], notice: "A person decides every case." } });
    render(<SourceOfFundsQueuePage />);
    expect(screen.getAllByText("A person decides every case.").length).toBeGreaterThan(0);
    expect(screen.getByText("Waiting for a reviewer", { selector: "span, div" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "abcdef12" }).getAttribute("href")).toBe("/admin/source-of-funds/abcdef1234567890");
    expect(screen.queryByText(/EMPLOYMENT/)).toBeNull();
  });

  it("says Not available, never an empty list, when the product line is switched off", () => {
    state.queries.queue = query({ isError: true, error: err(409, "service_unavailable") });
    render(<SourceOfFundsQueuePage />);
    expect(screen.getByText("Not available")).toBeTruthy();
    expect(screen.queryByText("No cases match this filter.")).toBeNull();
  });

  it("says plainly when nothing matches, which is not the same as being unavailable", () => {
    state.queries.queue = query({ data: { items: [], notice: "n" } });
    render(<SourceOfFundsQueuePage />);
    expect(screen.getByText("No cases match this filter.")).toBeTruthy();
    expect(screen.queryByText("Not available")).toBeNull();
  });

  it("says Withheld for your role, not zero cases, to a role that may not read the queue", () => {
    state.queries.queue = query({ isError: true, error: err(403, "protected_action_denied") });
    render(<SourceOfFundsQueuePage />);
    expect(screen.getByText("Withheld for your role")).toBeTruthy();
  });
});

describe("a case", () => {
  it("shows the questions in words with the answers, the evidence references and the notice", () => {
    state.queries.case = query({ data: baseCase });
    state.queries.questionnaire = query({ data: questionnaire });
    render(<SourceOfFundsCasePage caseId="abcdef1234567890" />);
    expect(screen.getByText("Where do the funds come from?")).toBeTruthy();
    expect(screen.getByText("EMPLOYMENT")).toBeTruthy();
    expect(screen.getByText("40000.00 GBP")).toBeTruthy();
    expect(screen.getByText("payslip")).toBeTruthy();
    expect(screen.getByText(/never clears anyone by itself/)).toBeTruthy();
    expect(screen.getByText("Waiting for a reviewer")).toBeTruthy();
  });

  it("will not send a decision until a decision and a reason are chosen, and sends exactly what was chosen", () => {
    state.queries.case = query({ data: baseCase });
    state.queries.questionnaire = query({ data: questionnaire });
    render(<SourceOfFundsCasePage caseId="abcdef1234567890" />);
    const send = screen.getByRole("button", { name: "Record decision" }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.click(screen.getByRole("radio", { name: /Do not clear/ }));
    expect(send.disabled).toBe(true); // a decision alone is not enough: a reason is required
    fireEvent.click(screen.getByRole("checkbox", { name: /The evidence is not enough/ }));
    expect(send.disabled).toBe(false);
    fireEvent.click(send);
    expect(state.decide).toHaveBeenCalledWith({ decision: "NOT_CLEAR", reason_codes: ["EVIDENCE_INSUFFICIENT"] });
  });

  it("offers no decision form for a case that is not waiting for one, and no appeal reasons on a first decision", () => {
    state.queries.case = query({ data: { ...baseCase, state: "CLEARED", valid_until: new Date(Date.now() + 864e5).toISOString() } });
    state.queries.questionnaire = query({ data: questionnaire });
    render(<SourceOfFundsCasePage caseId="abcdef1234567890" />);
    expect(screen.queryByRole("button", { name: "Record decision" })).toBeNull();
    expect(screen.getByText("Cleared")).toBeTruthy();
  });

  it("offers appeal reasons, and no ask-for-more, when deciding an appeal", () => {
    state.queries.case = query({ data: { ...baseCase, state: "APPEALED" } });
    state.queries.questionnaire = query({ data: questionnaire });
    render(<SourceOfFundsCasePage caseId="abcdef1234567890" />);
    expect(screen.getByText("Decide the appeal")).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: /The appeal brought new evidence/ })).toBeTruthy();
    expect(screen.queryByRole("radio", { name: /Ask for more/ })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: /The evidence is not enough/ })).toBeNull();
  });

  it("offers an appeal after a first adverse decision, and says it is not final", () => {
    state.queries.case = query({
      data: { ...baseCase, state: "NOT_CLEARED_PENDING_APPEAL", decisions: [{ decision: "NOT_CLEAR", from_state: "NEEDS_REVIEW", to_state: "NOT_CLEARED_PENDING_APPEAL", reason_codes: ["EVIDENCE_INSUFFICIENT"], decided_by: "r1", decided_at: "2026-11-16T11:00:00Z" }] },
    });
    state.queries.questionnaire = query({ data: questionnaire });
    render(<SourceOfFundsCasePage caseId="abcdef1234567890" />);
    expect(screen.getByText(/It is not final/)).toBeTruthy();
    expect(screen.getByText(/The evidence is not enough/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Appeal this decision" }));
    expect(state.appeal).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Record decision" })).toBeNull();
  });

  it("shows what is missing in words on an incomplete case", () => {
    state.queries.case = query({ data: { ...baseCase, state: "INCOMPLETE", problems: ["EVIDENCE_MISSING:SOF-01:BANK_STATEMENT", "ANSWER_MISSING:SOF-03"] } });
    state.queries.questionnaire = query({ data: questionnaire });
    render(<SourceOfFundsCasePage caseId="abcdef1234567890" />);
    expect(screen.getByText("SOF-01 needs evidence of type bank statement.")).toBeTruthy();
    expect(screen.getByText("No answer to SOF-03.")).toBeTruthy();
  });

  it("says the answers were erased, and does not offer a decision, when the person's data was erased", () => {
    state.queries.case = query({ data: { ...baseCase, answers: {}, evidence: [], erased: true, state: "CANCELED" } });
    render(<SourceOfFundsCasePage caseId="abcdef1234567890" />);
    expect(screen.getByText("Answers erased")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Record decision" })).toBeNull();
  });

  it("says Withheld for your role when the role may not read the answers", () => {
    state.queries.case = query({ isError: true, error: err(403, "protected_action_denied") });
    render(<SourceOfFundsCasePage caseId="abcdef1234567890" />);
    expect(screen.getByText("Withheld for your role")).toBeTruthy();
  });
});
