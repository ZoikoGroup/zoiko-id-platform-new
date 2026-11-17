/** Biometric methods console (ZID-DTV-WF-04 section 06; CR-107), rendered against a mocked client. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@zoiko/api-client";
import type { BiometricMethodVersion, BiometricProposal } from "@zoiko/api-client";
import { ToastProvider } from "@zoiko/ui";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

if (!(globalThis as { CSS?: unknown }).CSS) Object.defineProperty(globalThis, "CSS", { value: { escape: (v: string) => v.replace(/[^\w-]/g, (c) => `\\${c}`) } });

const fake = vi.hoisted(() => ({
  regulatory: {
    biometricMethods: vi.fn(),
    proposeBiometricMethod: vi.fn(),
    approveBiometricProposal: vi.fn(),
    rejectBiometricProposal: vi.fn(),
    stepDownBiometricMethod: vi.fn(),
  },
}));

vi.mock("@/lib/runtime", () => ({
  useRuntime: () => ({ api: fake, protect: <T,>(run: () => Promise<T>) => run(), session: { authenticated: true, subject: { sub: "op-1" }, csrf: "x" } }),
}));

import { BiometricMethodsPage, stepDownTargets } from "./biometric-methods";

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

const SCOPE = { method: "FACE_MATCH" as const, engine_id: "deepface-arcface", engine_version: "1.4.0", country: "GB", purpose: "IDENTITY_VERIFICATION", pilot_tenant: "11111111-2222-3333-4444-555555555555" };

function version(over: Partial<BiometricMethodVersion> = {}): BiometricMethodVersion {
  return {
    scope_id: "face-gb",
    version: 3,
    state: "PRODUCTION_ELIGIBLE",
    scope: SCOPE,
    dpia_ref: "DPIA-GB-7",
    evaluation_ref: "LAB-1",
    reason: "approved after the pilot",
    evidence_ref: "T-1",
    recorded_by: "ben",
    proposed_by: "ana",
    proposal_id: "p1",
    effective_at: "2026-11-17T10:00:00Z",
    ...over,
  };
}

const PROPOSAL: BiometricProposal = {
  proposal_id: "p2",
  scope_id: "face-gb",
  scope: SCOPE,
  from_version: 1,
  target: "PILOT_GATED",
  dpia_ref: "DPIA-GB-7",
  evaluation_ref: "LAB-1",
  reason: "lab results accepted",
  evidence_ref: "T-2",
  proposed_by: "op-1",
  proposed_at: "2026-11-17T09:00:00Z",
  status: "OPEN",
  decided_by: null,
  decided_at: null,
  decision_reason: null,
};

describe("step-down targets", () => {
  it("offers only less permissive states, pause first, and nothing from OFF", () => {
    expect(stepDownTargets("PRODUCTION_ELIGIBLE", "t1")).toEqual(["PAUSED", "PILOT_GATED", "LAB_ONLY", "OFF"]);
    expect(stepDownTargets("PRODUCTION_ELIGIBLE", null)).toEqual(["PAUSED", "LAB_ONLY", "OFF"]);
    expect(stepDownTargets("PAUSED", "t1")).toEqual(["OFF"]);
    expect(stepDownTargets("OFF", "t1")).toEqual([]);
  });
});

describe("biometric methods", () => {
  it("says every method is off when no scope exists", async () => {
    fake.regulatory.biometricMethods.mockResolvedValue({ items: [], open_proposals: [] });
    mount(<BiometricMethodsPage />);
    expect(await screen.findByText("No biometric method is on anywhere")).toBeTruthy();
  });

  it("pauses a scope at once with a reason and evidence", async () => {
    fake.regulatory.biometricMethods.mockResolvedValue({ items: [{ current: version(), versions: [version()] }], open_proposals: [] });
    fake.regulatory.stepDownBiometricMethod.mockResolvedValue(version({ state: "PAUSED", version: 4 }));
    mount(<BiometricMethodsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Step down face-gb" }));
    const dialog = await screen.findByRole("dialog");
    const pause = within(dialog).getByRole("button", { name: "Pause now" });
    expect(pause.hasAttribute("disabled") || pause.getAttribute("aria-disabled") === "true").toBe(true);
    await userEvent.type(within(dialog).getByLabelText(/Evidence reference/), "INC-9");
    await userEvent.type(within(dialog).getByLabelText(/^Reason/), "injection attack suspected");
    await userEvent.click(pause);
    await waitFor(() =>
      expect(fake.regulatory.stepDownBiometricMethod).toHaveBeenCalledWith("face-gb", { target: "PAUSED", reason: "injection attack suspected", evidence_ref: "INC-9" }),
    );
  });

  it("explains in words why the proposer cannot approve", async () => {
    fake.regulatory.biometricMethods.mockResolvedValue({ items: [], open_proposals: [PROPOSAL] });
    fake.regulatory.approveBiometricProposal.mockRejectedValue(new ApiError({ status: 403, code: "METHOD_APPROVED_BY_PROPOSER", message: "a different person approves a step up" }));
    mount(<BiometricMethodsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Approve face-gb" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/^Reason/), "evaluation report reviewed");
    await userEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    expect(await within(dialog).findByText(/You proposed this step up/)).toBeTruthy();
  });

  it("asks for the DPIA and evaluation references before proposing a pilot", async () => {
    const lab = version({ state: "LAB_ONLY", version: 1 });
    fake.regulatory.biometricMethods.mockResolvedValue({ items: [{ current: lab, versions: [lab] }], open_proposals: [] });
    fake.regulatory.proposeBiometricMethod.mockResolvedValue(PROPOSAL);
    mount(<BiometricMethodsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Propose next step for face-gb" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/^Evidence reference/), "T-2");
    await userEvent.type(within(dialog).getByLabelText(/^Reason/), "lab results accepted");
    const go = within(dialog).getByRole("button", { name: "Propose" });
    expect(go.hasAttribute("disabled") || go.getAttribute("aria-disabled") === "true").toBe(true);
    await userEvent.type(within(dialog).getByLabelText(/^DPIA reference/), "DPIA-GB-7");
    await userEvent.type(within(dialog).getByLabelText(/^Independent evaluation reference/), "LAB-1");
    await userEvent.click(go);
    await waitFor(() =>
      expect(fake.regulatory.proposeBiometricMethod).toHaveBeenCalledWith(
        expect.objectContaining({ target: "PILOT_GATED", dpia_ref: "DPIA-GB-7", evaluation_ref: "LAB-1", scope: SCOPE }),
      ),
    );
  });
});
