/** One press makes a build pipeline's credentials: the key pair is made in the browser, only its public half is registered, the private key is shown once. */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const shared = vi.hoisted(() => ({ create: vi.fn(), download: vi.fn() }));

vi.mock("@zoiko/ui", () => ({
  Banner: ({ title, children }: { title: string; children?: ReactNode }) => (
    <div>
      {title}
      {children}
    </div>
  ),
  Button: ({ children, onPress, isDisabled }: { children: ReactNode; onPress?: () => void; isDisabled?: boolean }) => (
    <button onClick={onPress} disabled={isDisabled}>
      {children}
    </button>
  ),
  CopyId: ({ value }: { value: string }) => <span>{value}</span>,
  Dialog: ({ isOpen, children, footer }: { isOpen: boolean; children: ReactNode; footer: ReactNode }) => (isOpen ? <div role="dialog">{children}{footer}</div> : null),
  TextField: ({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) => (
    <label>
      {label}
      <input aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  ),
}));
vi.mock("@/lib/cell", () => ({ ApiErrorView: ({ title }: { title?: string }) => <div role="alert">{title}</div> }));
vi.mock("@/lib/runtime", () => ({ useRuntime: () => ({ api: { integration: { createOauthClient: shared.create } }, protect: (f: () => unknown) => f() }) }));
vi.mock("@/lib/keypair", () => ({
  generateKeyPair: async () => ({
    publicKeys: JSON.stringify({ keys: [{ kty: "EC", crv: "P-256", x: "px", y: "py", kid: "pipeline-1" }] }),
    privateKey: JSON.stringify({ kty: "EC", crv: "P-256", x: "px", y: "py", d: "SECRET-D", kid: "pipeline-1" }),
  }),
  downloadText: shared.download,
}));

import { PipelineCredentialsDialog } from "./pipeline-credentials";

beforeEach(() => {
  shared.create.mockReset();
  shared.download.mockReset();
});
afterEach(cleanup);

describe("Create pipeline credentials", () => {
  it("registers a build-pipeline client with only the public key, then shows the client id, the identity to grant, and what is left to do", async () => {
    shared.create.mockResolvedValue({ client_id: "ci-bot-1a2b", service_principal_id: "11111111-2222-4333-8444-555555555555", scopes: ["machine.build:register"] });
    const user = userEvent.setup();
    render(<PipelineCredentialsDialog isOpen onClose={() => undefined} />);
    await user.click(screen.getByRole("button", { name: "Create credentials" }));

    const sent = shared.create.mock.calls[0]?.[0];
    expect(sent).toMatchObject({ name: "Build pipeline", client_type: "CONFIDENTIAL", purpose: "BUILD_PIPELINE" });
    expect(JSON.stringify(sent)).not.toContain("SECRET-D"); // the private half never leaves the browser
    expect(JSON.stringify(sent.jwks)).toContain("pipeline-1");

    expect(await screen.findByText("ci-bot-1a2b")).toBeTruthy();
    expect(screen.getByText("11111111-2222-4333-8444-555555555555")).toBeTruthy();
    expect(screen.getByText(/Save the private key now/)).toBeTruthy();
    expect(screen.getByText(/Register a software build/)).toBeTruthy();
    expect(screen.getByText(/cannot approve, revoke or run anything/)).toBeTruthy();
  });

  it("hands over the private key as a file, once", async () => {
    shared.create.mockResolvedValue({ client_id: "ci-bot-1a2b", service_principal_id: "p1" });
    const user = userEvent.setup();
    render(<PipelineCredentialsDialog isOpen onClose={() => undefined} />);
    await user.click(screen.getByRole("button", { name: "Create credentials" }));
    await user.click(await screen.findByRole("button", { name: "Download the private key" }));
    expect(shared.download).toHaveBeenCalledTimes(1);
    expect(shared.download.mock.calls[0]?.[0]).toBe("Build pipeline-private-key.json");
    expect(shared.download.mock.calls[0]?.[1]).toContain("SECRET-D");
  });

  it("uses the name the person gave", async () => {
    shared.create.mockResolvedValue({ client_id: "x", service_principal_id: null });
    const user = userEvent.setup();
    render(<PipelineCredentialsDialog isOpen onClose={() => undefined} />);
    const name = screen.getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Invoice bot CI");
    await user.click(screen.getByRole("button", { name: "Create credentials" }));
    expect(shared.create.mock.calls[0]?.[0]).toMatchObject({ name: "Invoice bot CI" });
    expect(screen.queryByText(/Identity to give the grant to/)).toBeNull(); // nothing to show when the server named none
  });

  it("says so when the credentials could not be created, and shows no key", async () => {
    shared.create.mockRejectedValue({ error: { code: "ENTITLEMENT_REQUIRED" } });
    const user = userEvent.setup();
    render(<PipelineCredentialsDialog isOpen onClose={() => undefined} />);
    await user.click(screen.getByRole("button", { name: "Create credentials" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText(/Save the private key now/)).toBeNull();
  });
});
