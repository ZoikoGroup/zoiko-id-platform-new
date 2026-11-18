/** WF-04 gap closure (CR-113), rendered against a mocked client: release drift on the readiness screen (QA-041), affected data classes on a breach
 * case (QA-034), provider deletions asked for again and alerted (QA-023), and the accessibility matrix (QA-044): the applicant's session page and the
 * biometric governance screens checked with axe in each layout direction and language they ship in. This is automated structural evidence in a
 * simulated browser: colour contrast, zoom and a screen-reader run on real devices stay a manual pass. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { BiometricMethodAnswer, BreachCase, ErasureJob, SubjectSessionStatus } from "@zoiko/api-client";
import { ToastProvider } from "@zoiko/ui";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

if (!(globalThis as { CSS?: unknown }).CSS) Object.defineProperty(globalThis, "CSS", { value: { escape: (v: string) => v.replace(/[^\w-]/g, (c) => `\\${c}`) } });

const fake = vi.hoisted(() => ({
  verification: { biometricEligibility: vi.fn() },
  privacy: {
    v2: {
      breaches: vi.fn(),
      openBreach: vi.fn(),
      addBreachDataClasses: vi.fn(),
      pauseCapture: vi.fn(),
      releaseCapture: vi.fn(),
      closeBreach: vi.fn(),
      notifyBreach: vi.fn(),
      breachDossier: vi.fn(),
      erasureJobs: vi.fn(),
      previewErasure: vi.fn(),
      openErasureJob: vi.fn(),
      attestBackup: vi.fn(),
      session: vi.fn(),
      acknowledgeNotice: vi.fn(),
      sessionConsent: vi.fn(),
      chooseRoute: vi.fn(),
      cancelSession: vi.fn(),
      myReviews: vi.fn(),
    },
    suspendProcessor: vi.fn(),
  },
}));

vi.mock("@/lib/runtime", () => ({
  useRuntime: () => ({ api: fake, protect: <T,>(run: () => Promise<T>) => run(), session: { authenticated: true, subject: { sub: "u-1" }, csrf: "x" } }),
}));

import { VerificationSessionView } from "../../account/verification-session";
import { BiometricSection } from "./biometric";
import { BreachesSection, DATA_CLASS, parseClasses } from "./breaches";
import { ErasureSection } from "./erasure";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  document.documentElement.removeAttribute("dir");
  document.documentElement.removeAttribute("lang");
});

function mount(node: ReactNode, lang = "en", dir: "ltr" | "rtl" = "ltr") {
  document.documentElement.setAttribute("lang", lang);
  document.documentElement.setAttribute("dir", dir);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <main lang={lang} dir={dir}>
          {node}
        </main>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

async function clean(container: HTMLElement) {
  const result = await axe.run(container, {
    rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"] },
  });
  expect(result.violations.map((v) => [v.id, v.nodes.map((n) => n.target)])).toEqual([]);
}

function answer(over: Partial<BiometricMethodAnswer> = {}): BiometricMethodAnswer {
  return {
    method: "FACE_MATCH",
    available: true,
    state: "PRODUCTION_ELIGIBLE",
    reasons: [],
    consent_required: false,
    scope_ref: "face-gb:v3",
    dpia_package_id: "pkg-1",
    dpia_blockers: [],
    breaches: [],
    missing_retention: [],
    evaluation_id: "e1",
    evaluation_blockers: [],
    exposure_percent: 100,
    release_drift: [],
    release_attestation: "a".repeat(64),
    ...over,
  };
}

const CASE: BreachCase = {
  breach_id: "b1",
  summary: "vendor bucket exposed",
  severity: "MODERATE",
  jurisdictions: ["GB"],
  subjects_estimate: 10,
  detected_at: "2026-11-17T08:00:00Z",
  state: "OPEN",
  capture_state: "NOT_PAUSED",
  capture_paused_by: null,
  capture_paused_at: null,
  capture_released_by: null,
  capture_released_at: null,
  capture_release_reason: null,
  opened_by: "u-1",
  closed_at: null,
  clocks: [{ jurisdiction: "GB", state: "RUNNING", hours: 72, due_at: "2026-11-20T08:00:00Z", notified_at: null }],
  data_classes: ["biometric.match_result"],
};

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

function eligibility(methods: BiometricMethodAnswer[], binding: "report" | "enforce" = "report") {
  return {
    country: "GB",
    purpose: "IDENTITY_VERIFICATION",
    engine: null,
    available_methods: methods.filter((m) => m.available).map((m) => m.method),
    methods,
    policy_hash: "f".repeat(64),
    notes: [],
    release: { build_ref: "sha256:build-two", sdk_version: "capture-4.3", binding },
  };
}

/* ---------------------------------------------------------------------------------------------------------------------------- QA-041 */

describe("release signed for this build (QA-041)", () => {
  it("reports a release signed for another build without refusing it, and names the running build", async () => {
    fake.verification.biometricEligibility.mockResolvedValue(eligibility([answer({ release_drift: ["RELEASE_BUILD_CHANGED"] })]));
    mount(<BiometricSection />);
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    const face = await screen.findByRole("list", { name: "Face match gates" });
    expect(within(face).getByText(/not the one this approval was signed for/)).toBeTruthy();
    expect(within(face).getByText(/Reported only/)).toBeTruthy();
    expect(screen.getByText("sha256:build-two")).toBeTruthy();
    expect(screen.getByText(/is reported\)/)).toBeTruthy();
  });

  it("says the method is refused when the deployment enforces it", async () => {
    fake.verification.biometricEligibility.mockResolvedValue(
      eligibility([answer({ available: false, reasons: ["RELEASE_SDK_CHANGED"], release_drift: ["RELEASE_SDK_CHANGED"] })], "enforce"),
    );
    mount(<BiometricSection />);
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    const face = await screen.findByRole("list", { name: "Face match gates" });
    expect(within(face).getByText(/capture SDK is not the one/)).toBeTruthy();
    expect(within(face).queryByText(/Reported only/)).toBeNull();
    expect(within(face).getByText("Release signed for this build").closest("li")?.textContent).toContain(": refuses");
  });
});

/* ---------------------------------------------------------------------------------------------------------------------------- QA-034 */

describe("affected data classes (QA-034)", () => {
  it("accepts codes only", () => {
    expect(parseClasses("biometric.face_template, identity.document biometric.face_template")).toEqual(["biometric.face_template", "identity.document"]);
    expect(["biometric.face_template", "identity.document", "contact"].every((c) => DATA_CLASS.test(c))).toBe(true);
    expect(["Jane Smith", "a@b.com", "Biometric.Face"].some((c) => DATA_CLASS.test(c))).toBe(false);
  });

  it("records data classes when a case is opened, and refuses a name", async () => {
    fake.privacy.v2.breaches.mockResolvedValue({ items: [] });
    fake.privacy.v2.openBreach.mockResolvedValue(CASE);
    mount(<BreachesSection />);
    await userEvent.click(await screen.findByRole("button", { name: "Open a case" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/^Summary/), "vendor bucket exposed");
    await userEvent.type(within(dialog).getByLabelText(/^Jurisdictions/), "gb");
    await userEvent.type(within(dialog).getByLabelText(/^Detected at/), "2026-11-17T08:00");
    const classes = within(dialog).getByLabelText(/^Affected data classes/);
    await userEvent.type(classes, "Jane Smith");
    const go = within(dialog).getByRole("button", { name: "Open case" });
    expect(go.hasAttribute("disabled") || go.getAttribute("aria-disabled") === "true").toBe(true);
    await userEvent.clear(classes);
    await userEvent.type(classes, "biometric.face_template, biometric.match_result");
    await userEvent.click(go);
    await waitFor(() =>
      expect(fake.privacy.v2.openBreach).toHaveBeenCalledWith(expect.objectContaining({ data_classes: ["biometric.face_template", "biometric.match_result"] })),
    );
  });

  it("shows the recorded classes and adds more later", async () => {
    fake.privacy.v2.breaches.mockResolvedValue({ items: [CASE] });
    fake.privacy.v2.addBreachDataClasses.mockResolvedValue({ ...CASE, data_classes: ["biometric.face_template", "biometric.match_result"] });
    mount(<BreachesSection />);
    expect(await screen.findByText("biometric.match_result")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /Add data classes for case/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/^Data classes/), "biometric.face_template");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(fake.privacy.v2.addBreachDataClasses).toHaveBeenCalledWith("b1", ["biometric.face_template"]));
  });
});

/* ---------------------------------------------------------------------------------------------------------------------------- QA-023 */

describe("provider deletions asked again (QA-023)", () => {
  it("shows how often a provider was asked, and an alert once the attempts ran out", async () => {
    const job = {
      job_id: "j1",
      subject_ref: "11111111-2222-3333-4444-555555555555",
      scope: "ALL",
      reason: "subject asked",
      legal_basis: "ART17",
      state: "FAILED",
      created_at: "2026-11-01T08:00:00Z",
      completed_at: null,
      tasks: [
        { task_id: "t1", target_kind: "PROVIDER", target_ref: "veriff", state: "PENDING", receipt_hash: null, failure: null, due_at: null, asset_classes: [], attempts: 2 },
        { task_id: "t2", target_kind: "PROVIDER", target_ref: "acme", state: "FAILED", receipt_hash: null, failure: "RETRIES_EXHAUSTED", due_at: null, asset_classes: [], attempts: 5 },
      ],
      biometric_classes: [],
    } as unknown as ErasureJob;
    fake.privacy.v2.erasureJobs.mockResolvedValue({ items: [job] });
    mount(<ErasureSection />);
    expect(await screen.findByText(/veriff \(pending\), asked again 2×/)).toBeTruthy();
    expect(screen.getByText(/acme \(failed\), no receipt after 5 requests: alerted/)).toBeTruthy();
  });
});

/* ---------------------------------------------------------------------------------------------------------------------------- QA-044 */

const MATRIX: { lang: string; dir: "ltr" | "rtl" }[] = [
  { lang: "en", dir: "ltr" },
  { lang: "ar", dir: "rtl" },
  { lang: "hi", dir: "ltr" },
];

describe.each(MATRIX)("accessibility matrix ($lang, $dir) (QA-044)", ({ lang, dir }) => {
  it("the person's verification session: notice, consent and alternatives", async () => {
    fake.privacy.v2.session.mockResolvedValue(session());
    const { container } = mount(<VerificationSessionView sessionRef="vs_1" />, lang, dir);
    await screen.findByText("Read and acknowledge the privacy notice below.");
    expect(screen.getByRole("checkbox", { name: /I agree to the biometric checks/ })).toBeTruthy();
    await clean(container);
  });

  it("the biometric readiness answer, with refusals and release drift", async () => {
    fake.verification.biometricEligibility.mockResolvedValue(
      eligibility([
        answer({ available: false, reasons: ["DPIA_NOT_READY", "RETENTION_NOT_SET:biometric.live_selfie"], dpia_blockers: ["DPIA_POLICY_CHANGED"], missing_retention: ["biometric.live_selfie"] }),
        answer({ method: "LIVENESS", release_drift: ["RELEASE_BUILD_CHANGED"] }),
      ]),
    );
    const { container } = mount(<BiometricSection />, lang, dir);
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    await screen.findByRole("list", { name: "Face match gates" });
    await clean(container);
  });

  it("the breach cases, and the dialog that opens one", async () => {
    fake.privacy.v2.breaches.mockResolvedValue({ items: [CASE] });
    const { container } = mount(<BreachesSection />, lang, dir);
    await screen.findByText("biometric.match_result");
    await clean(container);
    await userEvent.click(screen.getByRole("button", { name: "Open a case" }));
    await clean(await screen.findByRole("dialog"));
  });
});
