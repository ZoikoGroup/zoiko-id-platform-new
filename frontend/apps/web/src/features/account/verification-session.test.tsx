/** The person's own verification session and privacy choices (ZID-DTV-WF-04 B01, B02, B06; CR-108), rendered against a mocked client. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { SubjectSessionStatus } from "@zoiko/api-client";
import { ToastProvider } from "@zoiko/ui";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

if (!(globalThis as { CSS?: unknown }).CSS) Object.defineProperty(globalThis, "CSS", { value: { escape: (v: string) => v.replace(/[^\w-]/g, (c) => `\\${c}`) } });

const fake = vi.hoisted(() => ({
  privacy: {
    v2: { session: vi.fn(), acknowledgeNotice: vi.fn(), sessionConsent: vi.fn(), chooseRoute: vi.fn(), cancelSession: vi.fn(), myReviews: vi.fn() },
    myPreferences: vi.fn(),
    consentOptions: vi.fn(),
    myConsents: vi.fn(),
    withdrawConsent: vi.fn(),
    myRequests: vi.fn(),
    packs: vi.fn(),
  },
}));

vi.mock("@/lib/runtime", () => ({
  useRuntime: () => ({ api: fake, protect: <T,>(run: () => Promise<T>) => run(), session: { authenticated: true, subject: { sub: "u-1" }, csrf: "x" } }),
}));

import { PrivacyCenter, REQUEST_STATUS_TEXT } from "./privacy";
import { VerificationSessionView } from "./verification-session";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function mount(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <main>{node}</main>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

function session(over: Partial<SubjectSessionStatus> = {}): SubjectSessionStatus {
  return {
    session_ref: "vs_1",
    state: "OPEN",
    capture_enabled: false,
    route: "NONE",
    blockers: ["NOTICE_NOT_ACKNOWLEDGED", "PRIVACY_CONSENT_REQUIRED"],
    purpose_decision_id: "d1",
    purpose_code: "IDENTITY_VERIFICATION",
    region: "GB",
    alternative_mode: null,
    assisted_state: null,
    notice: { notice_version: "BIOMETRIC_NOTICE@2", content_uri: "https://example.test/notice", content_hash: "h", acknowledged: false },
    alternatives_available: true,
    alternatives: [
      { id: "ASSISTED_DOCUMENT_REVIEW", label: "A person reviews your document", biometric: false },
      { id: "IN_PERSON_REVIEW", label: "Verify in person", biometric: false },
    ],
    statement: "Choosing an alternative never counts against you.",
    checked_at: "2026-11-17T10:00:00Z",
    ...over,
  };
}

describe("verification session (B01, B02)", () => {
  it("explains each blocker in words and never pre-ticks consent", async () => {
    fake.privacy.v2.session.mockResolvedValue(session());
    mount(<VerificationSessionView sessionRef="vs_1" />);
    expect(await screen.findByText("Read and acknowledge the privacy notice below.")).toBeTruthy();
    expect(screen.getByText(/This check relies on your consent/)).toBeTruthy();
    const box = screen.getByRole("checkbox", { name: /I agree to the biometric checks/ }) as HTMLInputElement;
    expect(box.checked).toBe(false);
    const agree = screen.getByRole("button", { name: "Agree and continue" });
    expect(agree.hasAttribute("disabled") || agree.getAttribute("aria-disabled") === "true").toBe(true);
  });

  it("asks for consent only when the session relies on it", async () => {
    fake.privacy.v2.session.mockResolvedValue(session({ blockers: ["NOTICE_NOT_ACKNOWLEDGED"] }));
    mount(<VerificationSessionView sessionRef="vs_1" />);
    expect(await screen.findByRole("button", { name: "I have read the notice" })).toBeTruthy();
    expect(screen.queryByRole("checkbox", { name: /I agree/ })).toBeNull();
  });

  it("acknowledges the notice separately from consent", async () => {
    fake.privacy.v2.session.mockResolvedValue(session());
    fake.privacy.v2.acknowledgeNotice.mockResolvedValue({ ack_id: "a1", notice: "BIOMETRIC_NOTICE@2", acknowledged_at: "2026-11-17T10:01:00Z" });
    mount(<VerificationSessionView sessionRef="vs_1" />);
    await userEvent.click(await screen.findByRole("button", { name: "I have read the notice" }));
    await waitFor(() => expect(fake.privacy.v2.acknowledgeNotice).toHaveBeenCalledWith("vs_1", "BIOMETRIC_NOTICE@2", expect.any(String)));
    expect(fake.privacy.v2.sessionConsent).not.toHaveBeenCalled();
  });

  it("chooses another way without asking why", async () => {
    fake.privacy.v2.session.mockResolvedValue(session());
    fake.privacy.v2.chooseRoute.mockResolvedValue(session({ alternative_mode: "IN_PERSON_REVIEW", route: "ALTERNATIVE", blockers: ["NON_BIOMETRIC_ALTERNATIVE_CHOSEN"] }));
    mount(<VerificationSessionView sessionRef="vs_1" />);
    expect(await screen.findByText(/You do not need to tell us why/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Choose: Verify in person" }));
    await waitFor(() => expect(fake.privacy.v2.chooseRoute).toHaveBeenCalledWith("vs_1", "IN_PERSON_REVIEW"));
    expect(screen.queryByRole("textbox", { name: /reason/i })).toBeNull();
  });

  it("tells the person where the assisted route stands", async () => {
    fake.privacy.v2.session.mockResolvedValue(session({ alternative_mode: "ASSISTED_DOCUMENT_REVIEW", route: "ALTERNATIVE", assisted_state: "WAITING", blockers: ["NON_BIOMETRIC_ALTERNATIVE_CHOSEN"] }));
    mount(<VerificationSessionView sessionRef="vs_1" />);
    expect(await screen.findByText(/A person will help you verify another way/)).toBeTruthy();
  });
});

describe("privacy centre (B06)", () => {
  it("explains what withdrawing a consent does before it is done", async () => {
    fake.privacy.myPreferences.mockResolvedValue({ items: [] });
    fake.privacy.consentOptions.mockResolvedValue({ items: [] });
    fake.privacy.myRequests.mockResolvedValue({ items: [] });
    fake.privacy.packs.mockResolvedValue({ items: [] });
    fake.privacy.v2.myReviews.mockResolvedValue({
      items: [{ review_id: "r1", decision_ref: "d1", kind: "APPEAL", state: "OVERTURNED", statement: "s", correction_ref: null, created_at: "2026-11-01T10:00:00Z", due_at: "2026-12-01T10:00:00Z", decided_at: null, checked_at: null }],
    });
    fake.privacy.myConsents.mockResolvedValue({
      items: [{ consent_id: "c1", purpose_code: "BIOMETRIC_IDENTITY_VERIFICATION", status: "ACTIVE", notice_version: "N@1", granted_at: "2026-11-01T10:00:00Z" }],
    });
    fake.privacy.withdrawConsent.mockResolvedValue({ consent_id: "c1", status: "WITHDRAWN" });
    mount(<PrivacyCenter />);
    await userEvent.click(await screen.findByRole("button", { name: "Withdraw consent for BIOMETRIC_IDENTITY_VERIFICATION" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Decisions already made are not reversed/)).toBeTruthy();
    expect(fake.privacy.withdrawConsent).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Withdraw" }));
    await waitFor(() => expect(fake.privacy.withdrawConsent).toHaveBeenCalledWith("c1"));
    expect(screen.getByText(/the decision was reversed/)).toBeTruthy();
  });

  it("says what a limited answer means", () => {
    expect(REQUEST_STATUS_TEXT.PARTIALLY_DENIED).toMatch(/law requires it or a legal hold/);
  });
});
