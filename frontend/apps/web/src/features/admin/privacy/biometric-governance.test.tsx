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
    },
  },
}));

vi.mock("@/lib/runtime", () => ({
  useRuntime: () => ({ api: fake, protect: <T,>(run: () => Promise<T>) => run(), session: { authenticated: true, subject: { sub: "u-1" }, csrf: "x" } }),
}));

import { BiometricSection } from "./biometric";
import { BreachesSection, pausesByDefault } from "./breaches";
import { band, DpiaSection } from "./dpia";
import { ErasureSection } from "./erasure";

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
        }),
        answer({ method: "LIVENESS", available: true, state: "PRODUCTION_ELIGIBLE" }),
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
    expect(screen.getByText("Available")).toBeTruthy();
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
