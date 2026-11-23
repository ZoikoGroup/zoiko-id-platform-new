/** The three steps from sign-up to onboarding, rendered for real against a mocked client: it shows what the server decided and offers the one step to do now. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const fake = vi.hoisted(() => ({ idv: { cases: vi.fn() }, tenant: { me: vi.fn() } }));

vi.mock("@/lib/runtime", () => ({
  useRuntime: () => ({ api: fake, protect: <T,>(run: () => Promise<T>) => run(), session: { authenticated: true, subject: { sub: "u-1" }, csrf: "x" } }),
}));

import { VerificationJourney } from "./verification-journey";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <VerificationJourney />
    </QueryClientProvider>,
  );
}

const steps = () => within(screen.getByRole("list", { name: "Verification steps" })).getAllByRole("listitem");

describe("get your company verified", () => {
  it("starts with identity and offers only that step", async () => {
    fake.idv.cases.mockResolvedValue({ items: [] });
    fake.tenant.me.mockResolvedValue({ status: "ORGANIZATION_VERIFYING" });
    mount();
    await screen.findByRole("list", { name: "Verification steps" });
    const [identity, company, onboarding] = steps();
    expect(within(identity!).getByRole("link", { name: "Verify your identity" }).getAttribute("href")).toContain("/account/identity-proofing?next=");
    expect(within(company!).queryByRole("link")).toBeNull(); // locked until identity is done
    expect(within(onboarding!).queryByRole("link")).toBeNull();
    expect(identity!.getAttribute("aria-current")).toBe("step");
  });

  it("offers the company step, and nothing else, once identity is approved", async () => {
    fake.idv.cases.mockResolvedValue({ items: [{ decision: "APPROVED" }] });
    fake.tenant.me.mockResolvedValue({ status: "ORGANIZATION_VERIFYING" });
    mount();
    expect(await screen.findByText("Your identity is verified.")).toBeTruthy();
    const [identity, company, onboarding] = steps();
    expect(within(identity!).queryByRole("link")).toBeNull();
    expect(within(company!).getByRole("link", { name: "Verify your company" }).getAttribute("href")).toBe("/admin/organization/claim");
    expect(within(onboarding!).queryByRole("link")).toBeNull();
  });

  it("opens onboarding once the company is verified", async () => {
    fake.idv.cases.mockResolvedValue({ items: [{ decision: "APPROVED" }] });
    fake.tenant.me.mockResolvedValue({ status: "CONFIGURING" });
    mount();
    expect(await screen.findByText(/Your company is verified\. Set up domains/)).toBeTruthy();
    const [, , onboarding] = steps();
    expect(within(onboarding!).getByRole("link", { name: "Go to onboarding" }).getAttribute("href")).toBe("/admin/onboarding");
    expect(onboarding!.getAttribute("aria-current")).toBe("step");
  });

  it("does not call a person verified because a check was started", async () => {
    fake.idv.cases.mockResolvedValue({ items: [{ decision: "REVIEW_REQUIRED" }, { decision: "INDETERMINATE" }] });
    fake.tenant.me.mockResolvedValue(null);
    mount();
    expect(await screen.findByText(/Photograph an identity document/)).toBeTruthy();
    expect(screen.queryByText("Your identity is verified.")).toBeNull();
  });
});
