/** Biometric governance screens (ZID-DTV-WF-04 B03, B09, B11; CR-107, CR-108): readiness in words, breach containment, and the DPIA workbench,
 * rendered against a mocked client. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@zoiko/api-client";
import type { BiometricMethodAnswer, BreachCase } from "@zoiko/api-client";
import { ToastProvider } from "@zoiko/ui";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

if (!(globalThis as { CSS?: unknown }).CSS) Object.defineProperty(globalThis, "CSS", { value: { escape: (v: string) => v.replace(/[^\w-]/g, (c) => `\\${c}`) } });

const fake = vi.hoisted(() => ({
  verification: { biometricEligibility: vi.fn() },
  privacy: {
    v2: {
      dpiaPackages: vi.fn(),
      dpiaPackage: vi.fn(),
      createDpiaPackage: vi.fn(),
      breaches: vi.fn(),
      openBreach: vi.fn(),
      pauseCapture: vi.fn(),
      releaseCapture: vi.fn(),
      closeBreach: vi.fn(),
      notifyBreach: vi.fn(),
      erasureJobs: vi.fn(),
      previewErasure: vi.fn(),
      openErasureJob: vi.fn(),
      attestBackup: vi.fn(),
      reviews: vi.fn(),
      decideReview: vi.fn(),
      checkReview: vi.fn(),
      models: vi.fn(),
      registerModel: vi.fn(),
      recordEvaluation: vi.fn(),
      promoteModel: vi.fn(),
      transfers: vi.fn(),
      checkTransfer: vi.fn(),
      changes: vi.fn(),
      notifyChange: vi.fn(),
      decideChange: vi.fn(),
      oversight: vi.fn(),
      assistedQueue: vi.fn(),
      claimAssisted: vi.fn(),
      completeAssisted: vi.fn(),
    },
  },
}));

vi.mock("@/lib/runtime", () => ({
  useRuntime: () => ({ api: fake, protect: <T,>(run: () => Promise<T>) => run(), session: { authenticated: true, subject: { sub: "u-1" }, csrf: "x" } }),
}));

import { AssistedSection } from "./assisted";
import { BiometricSection } from "./biometric";
import { BreachesSection, pausesByDefault } from "./breaches";
import { band, DpiaSection } from "./dpia";
import { ErasureSection } from "./erasure";
import { ModelsSection } from "./models";
import { OversightSection } from "./oversight";
import { ReviewsSection } from "./reviews";
import { TransfersSection } from "./transfers";

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

function answer(over: Partial<BiometricMethodAnswer> = {}): BiometricMethodAnswer {
  return {
    method: "FACE_MATCH",
    available: false,
    state: "PILOT_GATED",
    reasons: [],
    consent_required: false,
    scope_ref: "face-gb:v2",
    dpia_package_id: "pkg-1",
    dpia_blockers: [],
    breaches: [],
    missing_retention: [],
    evaluation_id: null,
    evaluation_blockers: [],
    exposure_percent: 100,
    ...over,
  };
}

const CASE: BreachCase = {
  breach_id: "b1",
  summary: "vendor bucket exposed",
  severity: "HIGH",
  jurisdictions: ["GB"],
  subjects_estimate: 10,
  detected_at: "2026-11-17T08:00:00Z",
  state: "OPEN",
  capture_state: "PAUSED",
  capture_paused_by: "u-1",
  capture_paused_at: "2026-11-17T08:05:00Z",
  capture_released_by: null,
  capture_released_at: null,
  capture_release_reason: null,
  opened_by: "u-1",
  closed_at: null,
  clocks: [{ jurisdiction: "GB", state: "RUNNING", hours: 72, due_at: "2026-11-20T08:00:00Z", notified_at: null }],
};

describe("biometric readiness", () => {
  it("names every refusing gate in words, including missing retention and a breach pause", async () => {
    fake.verification.biometricEligibility.mockResolvedValue({
      country: "GB",
      purpose: "IDENTITY_VERIFICATION",
      engine: { engine_id: "deepface-arcface" },
      available_methods: [],
      methods: [
        answer({
          reasons: ["DPIA_NOT_READY", "BREACH_CAPTURE_PAUSED", "RETENTION_NOT_SET:biometric.live_selfie"],
          dpia_blockers: ["DPIA_EXPIRED"],
          breaches: ["b1"],
          missing_retention: ["biometric.live_selfie"],
          evaluation_blockers: ["EVALUATION_STALE"],
        }),
        answer({ method: "LIVENESS", available: true, state: "PRODUCTION_ELIGIBLE", exposure_percent: 25 }),
      ],
      policy_hash: "f".repeat(64),
      notes: [],
    });
    mount(<BiometricSection />);
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText(/No biometric method is available in GB/)).toBeTruthy();
    expect(fake.verification.biometricEligibility).toHaveBeenCalledWith("GB", "IDENTITY_VERIFICATION");
    const face = screen.getByRole("list", { name: "Face match gates" });
    expect(within(face).getByText("DPIA_EXPIRED")).toBeTruthy();
    expect(within(face).getByText("biometric.live_selfie")).toBeTruthy();
    expect(within(face).getByText(/Release it on the Breaches tab/)).toBeTruthy();
    expect(within(face).getByText("EVALUATION_STALE")).toBeTruthy();
    expect(screen.getByText("Available")).toBeTruthy();
    expect(screen.getByText(/runs for 25% of people/)).toBeTruthy();
  });
});

describe("biometric readiness: ecosystem use case", () => {
  it("explains a Zoiko product use that is not signed yet", async () => {
    fake.verification.biometricEligibility.mockResolvedValue({
      country: "GB",
      purpose: "ZOIKO_ROOMS_RENTER_IDENTITY",
      engine: null,
      available_methods: [],
      methods: [answer({ reasons: ["USE_CASE_NOT_CERTIFIED:ZOIKO_ROOMS_RENTER_IDENTITY"] })],
      policy_hash: "f".repeat(64),
      notes: [],
    });
    mount(<BiometricSection />);
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText(/has no signed integration acceptance yet/)).toBeTruthy();
  });
});

describe("breach cases", () => {
  it("pauses biometrics by default for HIGH and CRITICAL only", () => {
    expect([pausesByDefault("LOW"), pausesByDefault("MODERATE"), pausesByDefault("HIGH"), pausesByDefault("CRITICAL")]).toEqual([false, false, true, true]);
  });

  it("leaves the pause to the severity default unless the person changes it", async () => {
    fake.privacy.v2.breaches.mockResolvedValue({ items: [] });
    fake.privacy.v2.openBreach.mockResolvedValue(CASE);
    mount(<BreachesSection />);
    await userEvent.click(await screen.findByRole("button", { name: "Open a case" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Default for HIGH: paused/)).toBeTruthy();
    await userEvent.type(within(dialog).getByLabelText(/^Summary/), "vendor bucket exposed");
    await userEvent.type(within(dialog).getByLabelText(/^Jurisdictions/), "gb, fr");
    await userEvent.type(within(dialog).getByLabelText(/^Detected at/), "2026-11-17T08:00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Open case" }));
    await waitFor(() =>
      expect(fake.privacy.v2.openBreach).toHaveBeenCalledWith(expect.objectContaining({ severity: "HIGH", jurisdictions: ["GB", "FR"], pause_biometric_capture: null })),
    );
  });

  it("explains that whoever paused cannot release", async () => {
    fake.privacy.v2.breaches.mockResolvedValue({ items: [CASE] });
    fake.privacy.v2.releaseCapture.mockRejectedValue(new ApiError({ status: 403, code: "RELEASED_BY_WHOEVER_PAUSED", message: "no" }));
    mount(<BreachesSection />);
    expect(await screen.findByText("Biometrics paused")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /Release biometric pause/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/Why it is safe again/), "exposure closed and verified");
    await userEvent.click(within(dialog).getByRole("button", { name: "Release" }));
    expect(await within(dialog).findByText(/A different person releases it/)).toBeTruthy();
  });
});

describe("DPIA workbench", () => {
  it("bands scores as the server does", () => {
    expect([band(5).label, band(6).label, band(12).label, band(18).label]).toEqual(["Low", "Moderate", "High", "Critical"]);
  });

  it("lists packages with their release state", async () => {
    fake.privacy.v2.dpiaPackages.mockResolvedValue({
      items: [
        {
          package_id: "pkg-1",
          market: "GB",
          product: "biometric.face_match",
          version: "1.4.0",
          controller: "Acme Ltd",
          purpose_code: "IDENTITY_VERIFICATION",
          provider_service_ref: null,
          model_version: null,
          state: "DRAFT",
          approved_until: null,
          consultation_ref: null,
          created_by: "u-1",
          created_at: "2026-11-17T08:00:00Z",
          risk_count: 2,
          gates_signed: ["G0:PRODUCT"],
          release: { ready: false, blockers: ["DPIA_NOT_APPROVED"] },
        },
      ],
    });
    mount(<DpiaSection />);
    expect(await screen.findByText("GB · biometric.face_match · 1.4.0")).toBeTruthy();
    expect(screen.getByText("Not ready")).toBeTruthy();
    expect(screen.getByText("1 / 14")).toBeTruthy();
  });
});

describe("erasure console", () => {
  const SUBJECT = "11111111-2222-3333-4444-555555555555";

  it("previews an erasure in deletion order without opening a job", async () => {
    fake.privacy.v2.erasureJobs.mockResolvedValue({ items: [] });
    fake.privacy.v2.previewErasure.mockResolvedValue({
      subject_ref: SUBJECT,
      scope: "ALL",
      dry_run: true,
      plan: [
        { target: "vault:1", object_type: "biometric.live_selfie", asset_class: "biometric.live_selfie", action: "CRYPTO_SHRED" },
        { target: "vault:2", object_type: "biometric.match_result", asset_class: "biometric.match_result", action: "HOLD", why: "LEGAL_HOLD" },
        { target: "acme:3", object_type: "verification.document", asset_class: null, action: "PROVIDER_DELETION", provider: "acme", sla_days: 30 },
      ],
      backups: { action: "ATTEST_LAST", window_days: 35 },
      statement: "Nothing was deleted.",
    });
    mount(<ErasureSection />);
    await userEvent.type(screen.getByLabelText(/Subject principal ID/), SUBJECT);
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(await screen.findByText("Crypto-shred")).toBeTruthy();
    expect(screen.getByText("Kept: hold")).toBeTruthy();
    expect(screen.getByText("acme, 30 days")).toBeTruthy();
    expect(screen.getByText(/within 35 days/)).toBeTruthy();
    expect(fake.privacy.v2.previewErasure).toHaveBeenCalledWith(SUBJECT, "ALL");
    expect(fake.privacy.v2.openErasureJob).not.toHaveBeenCalled();
  });

  it("refuses to attest backups before the other deletions, in words", async () => {
    fake.privacy.v2.erasureJobs.mockResolvedValue({
      items: [
        {
          job_id: "j1",
          subject_ref: SUBJECT,
          scope: "ALL",
          reason: "rights request",
          legal_basis: "art 17",
          state: "PENDING",
          created_at: "2026-11-17T08:00:00Z",
          completed_at: null,
          tasks: [
            { task_id: "t1", target_kind: "PROVIDER", target_ref: "acme", state: "PENDING", receipt_hash: null, failure: null, due_at: null, asset_classes: ["biometric.live_selfie"] },
            { task_id: "t2", target_kind: "BACKUP", target_ref: "cell-backups", state: "PENDING", receipt_hash: null, failure: null, due_at: null, asset_classes: [] },
          ],
          biometric_classes: [{ asset_class: "biometric.live_selfie", state: "PENDING", tasks: 2, outstanding: [] }],
        },
      ],
    });
    fake.privacy.v2.attestBackup.mockRejectedValue(new ApiError({ status: 409, code: "BACKUP_BEFORE_SOURCES", message: "no" }));
    mount(<ErasureSection />);
    expect(await screen.findByText(/live_selfie/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Attest backups" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/^Statement/), "purged from nightly backups");
    await userEvent.click(within(dialog).getByRole("button", { name: "Attest" }));
    expect(await within(dialog).findByText(/still outstanding/)).toBeTruthy();
  });
});

describe("appeals and reviews (B08)", () => {
  const REVIEW = {
    review_id: "r1",
    decision_ref: "dec_face_1",
    kind: "APPEAL" as const,
    subject_ref: "p-9",
    statement: "The face check failed because of my camera.",
    state: "OPEN" as const,
    requested_by: "p-9",
    reviewer: null,
    rationale: null,
    correction_ref: null,
    proposed_outcome: null,
    checker: null,
    checked_at: null,
    created_at: "2026-11-17T08:00:00Z",
    due_at: "2099-12-17T08:00:00Z",
    decided_at: null,
  };

  it("proposes a reversal and says a third person must confirm it", async () => {
    fake.privacy.v2.reviews.mockResolvedValue({ items: [REVIEW] });
    fake.privacy.v2.decideReview.mockResolvedValue({ ...REVIEW, state: "AWAITING_CHECKER", proposed_outcome: "OVERTURNED", reviewer: "u-1" });
    mount(<ReviewsSection />);
    await userEvent.click(await screen.findByRole("button", { name: "Review dec_face_1" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/my camera/)).toBeTruthy();
    await userEvent.click(within(dialog).getByRole("button", { name: /Outcome/ }));
    await userEvent.click(await screen.findByRole("option", { name: /Overturn it/ }));
    expect(within(dialog).getByText(/only after a third person/)).toBeTruthy();
    await userEvent.type(within(dialog).getByLabelText(/^Rationale/), "capture quality, not a mismatch");
    await userEvent.click(within(dialog).getByRole("button", { name: "Propose" }));
    await waitFor(() =>
      expect(fake.privacy.v2.decideReview).toHaveBeenCalledWith("r1", { outcome: "OVERTURNED", rationale: "capture quality, not a mismatch", correction_ref: null }),
    );
  });

  it("never offers the requester their own review or check", async () => {
    fake.privacy.v2.reviews.mockResolvedValue({ items: [{ ...REVIEW, requested_by: "u-1" }, { ...REVIEW, review_id: "r2", decision_ref: "dec_2", state: "AWAITING_CHECKER", reviewer: "u-1", proposed_outcome: "OVERTURNED" }] });
    mount(<ReviewsSection />);
    expect(await screen.findByText("You asked")).toBeTruthy();
    expect(screen.getByText("You reviewed")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Review dec_face_1/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Check dec_2/ })).toBeNull();
  });

  it("explains a refused check in words", async () => {
    fake.privacy.v2.reviews.mockResolvedValue({ items: [{ ...REVIEW, state: "AWAITING_CHECKER", reviewer: "u-2", proposed_outcome: "OVERTURNED", rationale: "capture quality" }] });
    fake.privacy.v2.checkReview.mockRejectedValue(new ApiError({ status: 403, code: "CHECKER_IS_REVIEWER", message: "no" }));
    mount(<ReviewsSection />);
    await userEvent.click(await screen.findByRole("button", { name: "Check dec_face_1" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/Your note/), "agree with the rationale");
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));
    expect(await within(dialog).findByText(/A different person checks it/)).toBeTruthy();
    expect(fake.privacy.v2.checkReview).toHaveBeenCalledWith("r1", { confirm: true, note: "agree with the rationale" });
  });
});

describe("models and fairness (B07)", () => {
  const BASE = {
    model_id: "m1",
    model_ref: "veriff",
    version: "2026.11",
    kind: "face_match" as const,
    provider_service_ref: null,
    live_features: [],
    state: "REGISTERED" as const,
    state_reason: null,
    promoted_at: null,
  };

  it("never says pass, and only promotes a version that passes with limits", async () => {
    fake.privacy.v2.models.mockResolvedValue({
      items: [
        { ...BASE, fairness: { state: "DATA_INSUFFICIENT", reasons: ["EVALUATION_HAS_NO_COHORT_COMPARISON"] }, latest_evaluation: null },
        {
          ...BASE,
          model_id: "m2",
          version: "2026.12",
          fairness: { state: "PASS_WITH_LIMITS", reasons: [] },
          latest_evaluation: {
            evaluation_id: "e1",
            evaluator_ref: "Lab A",
            independent: true,
            cohorts: 4,
            metrics: { fmr: { value: 0.001, ci_low: 0.0005, ci_high: 0.002 }, fnmr: { value: 0.01, ci_low: 0.008, ci_high: 0.012 }, failure_to_acquire: { value: 0.02, ci_low: 0.01, ci_high: 0.03 } },
            accessible_alternative_confirmed: true,
            report_ref: null,
            created_at: "2026-11-01T10:00:00Z",
          },
        },
      ],
    });
    mount(<ModelsSection />);
    expect(await screen.findByText("Data insufficient")).toBeTruthy();
    expect(screen.getByText("Passes, with limits")).toBeTruthy();
    expect(screen.queryByText(/^Pass$/)).toBeNull();
    const blocked = screen.getByRole("button", { name: "Promote veriff@2026.11" });
    expect(blocked.hasAttribute("disabled") || blocked.getAttribute("aria-disabled") === "true").toBe(true);
    expect(screen.getByText((_, el) => el?.tagName === "LI" && (el.textContent ?? "").includes("0.10% (95% CI 0.05%–0.20%)"))).toBeTruthy();
  });

  it("refuses an evaluation whose value is outside its interval", async () => {
    fake.privacy.v2.models.mockResolvedValue({ items: [{ ...BASE, fairness: { state: "NOT_RUN", reasons: ["NO_EVALUATION"] }, latest_evaluation: null }] });
    mount(<ModelsSection />);
    await userEvent.click(await screen.findByRole("button", { name: "Record evaluation for veriff@2026.11" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/^Evaluator/), "Lab A");
    for (const k of ["fmr", "fnmr", "failure_to_acquire"]) {
      await userEvent.type(within(dialog).getByLabelText(new RegExp(`^${k} value`)), "0.5");
      await userEvent.type(within(dialog).getByLabelText(new RegExp(`^${k} CI low`)), "0.1");
      await userEvent.type(within(dialog).getByLabelText(new RegExp(`^${k} CI high`)), "0.2");
    }
    const rec = within(dialog).getByRole("button", { name: "Record" });
    expect(rec.hasAttribute("disabled") || rec.getAttribute("aria-disabled") === "true").toBe(true);
  });
});

describe("transfers and subprocessors (B04)", () => {
  it("explains a refused transfer in words, including support access from abroad", async () => {
    fake.privacy.v2.transfers.mockResolvedValue({ items: [] });
    fake.privacy.v2.changes.mockResolvedValue({ items: [] });
    fake.privacy.v2.checkTransfer.mockResolvedValue({ allowed: false, reason_codes: ["SUBPROCESSOR_CHANGE_PENDING", "REMOTE_ACCESS_FROM_IN_NOT_APPROVED"], mechanism: null });
    mount(<TransfersSection />);
    await userEvent.type(await screen.findByLabelText(/^Processor ID/), "veriff");
    await userEvent.type(screen.getByLabelText(/^To /), "us");
    await userEvent.type(screen.getByLabelText(/^Support access from/), "in");
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText("Refused")).toBeTruthy();
    expect(screen.getByText(/egress is blocked until then/)).toBeTruthy();
    expect(screen.getByText("Support access from IN is not an approved location.")).toBeTruthy();
    expect(fake.privacy.v2.checkTransfer).toHaveBeenCalledWith(
      expect.objectContaining({ processor_id: "veriff", destination_country: "US", remote_access_countries: ["IN"], data_category: "D3" }),
    );
  });

  it("decides a waiting processor change", async () => {
    fake.privacy.v2.transfers.mockResolvedValue({ items: [] });
    fake.privacy.v2.changes.mockResolvedValue({
      items: [{ change_id: "c1", processor_id: "veriff", kind: "LOCATION_ADDED", detail: "us-east", state: "PENDING_APPROVAL", notified_at: "2026-11-17T08:00:00Z", requested_by: "u-2", decided_by: null, decided_at: null, created_at: "2026-11-17T08:00:00Z" }],
    });
    fake.privacy.v2.decideChange.mockResolvedValue({ state: "APPROVED" });
    mount(<TransfersSection />);
    await userEvent.click(await screen.findByRole("button", { name: "Approve change us-east" }));
    await waitFor(() => expect(fake.privacy.v2.decideChange).toHaveBeenCalledWith("c1", true));
  });
});

describe("oversight monitor (B12)", () => {
  it("names what needs attention and where to act", async () => {
    fake.privacy.v2.oversight.mockResolvedValue({
      window_days: 30,
      breaches_pausing_capture: 1,
      dpia: { approved: 2, expiring_30d: 1, lapsed: 0 },
      models: { active: 1, held: 1 },
      reviews: { open: 3, awaiting_checker: 1, overdue: 2 },
      erasure: { open: 0, held: 0, failed: 0, provider_tasks_overdue: 0 },
      consent: { withdrawn_30d: 4 },
      subprocessor_changes_waiting: 0,
      transfers_refused_30d: 0,
      retention_missing_classes: [],
      attention: ["BREACH_PAUSING_CAPTURE", "MODEL_HELD", "REVIEWS_OVERDUE"],
      measured_at: "2026-11-17T10:00:00Z",
    });
    mount(<OversightSection />);
    const list = await screen.findByRole("list", { name: "Needs attention" });
    expect(within(list).getByText(/pausing biometric capture/)).toBeTruthy();
    expect(within(list).getByText(/Models & fairness tab/)).toBeTruthy();
    expect(screen.getByText("2 overdue")).toBeTruthy();
  });
});

describe("assisted verification (B02)", () => {
  const ITEM = {
    session_ref: "vs_1",
    subject_principal_id: "p-9",
    purpose_code: "IDENTITY_VERIFICATION",
    country: "GB",
    alternative_mode: "IN_PERSON_REVIEW",
    assisted_state: "WAITING" as const,
    assisted_by: null,
    assisted_at: null,
    assisted_outcome_ref: null,
    assisted_reason_code: null,
    created_at: "2026-11-17T08:00:00Z",
  };

  it("takes a waiting case and never offers a person their own", async () => {
    fake.privacy.v2.assistedQueue.mockResolvedValue({ items: [ITEM, { ...ITEM, session_ref: "vs_2", subject_principal_id: "u-1" }] });
    fake.privacy.v2.claimAssisted.mockResolvedValue({ session_ref: "vs_1", assisted_state: "IN_PROGRESS" });
    mount(<AssistedSection />);
    expect(await screen.findByText("Your own")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Take case vs_2" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Take case vs_1" }));
    await waitFor(() => expect(fake.privacy.v2.claimAssisted).toHaveBeenCalledWith("vs_1"));
  });

  it("records a case that could not be completed with a reason code, never a personal reason", async () => {
    fake.privacy.v2.assistedQueue.mockResolvedValue({ items: [{ ...ITEM, assisted_state: "IN_PROGRESS", assisted_by: "u-1" }] });
    fake.privacy.v2.completeAssisted.mockResolvedValue({ session_ref: "vs_1", assisted_state: "NOT_COMPLETED" });
    mount(<AssistedSection />);
    await userEvent.click(await screen.findByRole("button", { name: "Record outcome for vs_1" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /Outcome/ }));
    await userEvent.click(await screen.findByRole("option", { name: "Could not be completed" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Record" }));
    await waitFor(() => expect(fake.privacy.v2.completeAssisted).toHaveBeenCalledWith("vs_1", { done: false, reason_code: "PERSON_UNREACHABLE" }));
  });
});
