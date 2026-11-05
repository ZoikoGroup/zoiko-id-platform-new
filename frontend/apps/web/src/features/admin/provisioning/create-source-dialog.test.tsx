/** Register-source dialog: sends exactly the three request fields, shows the returned source, and reads the commercial / length refusals. */
import { ApiError } from "@zoiko/api-client";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const shared = vi.hoisted(() => ({
  error: null as unknown,
  mutate: vi.fn(),
  onSuccess: undefined as undefined | ((r: unknown) => void),
}));

vi.mock("@/lib/cell", () => ({
  useCellMutation: (_fn: unknown, opts: { onSuccess?: (r: unknown) => void }) => {
    shared.onSuccess = opts.onSuccess;
    return { mutate: shared.mutate, isPending: false, error: shared.error, reset: () => undefined };
  },
  ApiErrorView: () => <div>generic error</div>,
}));

// react-aria's overlays and fields need one shared React copy; plain stand-ins keep this about the form's behaviour.
vi.mock("@zoiko/ui", () => ({
  Dialog: ({ title, children, footer }: { title: string; children: ReactNode; footer: ReactNode }) => (
    <div>
      <h2>{title}</h2>
      {children}
      {footer}
    </div>
  ),
  Banner: ({ title, children }: { title: string; children: ReactNode }) => (
    <div>
      <strong>{title}</strong>
      {children}
    </div>
  ),
  Button: ({ children, onPress, isDisabled }: { children: ReactNode; onPress?: () => void; isDisabled?: boolean }) => (
    <button onClick={onPress} disabled={isDisabled}>
      {children}
    </button>
  ),
  RiskBadge: () => null,
  RadioCards: ({ options, value, onChange }: { options: { id: string; label: string }[]; value: string; onChange: (v: string) => void }) => (
    <div>
      {options.map((o) => (
        <button key={o.id} aria-pressed={value === o.id} onClick={() => onChange(o.id)}>
          {o.label}
        </button>
      ))}
    </div>
  ),
  TextField: ({ label, value, onChange, errorMessage }: { label: string; value: string; onChange: (v: string) => void; errorMessage?: string }) => (
    <label>
      {label}
      <input value={value} onChange={(e) => onChange(e.target.value)} />
      {errorMessage ? <span>{errorMessage}</span> : null}
    </label>
  ),
}));
vi.mock("./connection-details", () => ({ ConnectionDetails: () => null }));

const made = vi.hoisted(() => ({
  publicKeys: JSON.stringify({ keys: [{ kty: "EC", crv: "P-256", kid: "scim-made1", alg: "ES256", use: "sig", x: "px", y: "py" }] }),
  privateKey: JSON.stringify({ kty: "EC", crv: "P-256", kid: "scim-made1", x: "px", y: "py", d: "SECRET-D" }),
  download: vi.fn(),
}));
vi.mock("@/lib/keypair", () => ({
  generateKeyPair: vi.fn(async () => ({ publicKeys: made.publicKeys, privateKey: made.privateKey })),
  downloadText: made.download,
}));

import { CLIENT_MAX, CreateSourceDialog, NAME_MAX } from "./create-source-dialog";

afterEach(() => {
  cleanup();
  shared.error = null;
  shared.mutate.mockReset();
  made.download.mockReset();
});

const KEYS = JSON.stringify({ keys: [{ kty: "EC", crv: "P-256", kid: "scim-1", x: "x", y: "y" }] });

async function pickExisting(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /Use an existing OAuth client ID/ }));
}

describe("CreateSourceDialog", () => {
  it("creates the connector's client from pasted public keys by default", async () => {
    render(<CreateSourceDialog isOpen onClose={() => undefined} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/Display name/), "  Workday HR ");
    fireEvent.change(screen.getByLabelText(/Public keys/), { target: { value: KEYS } });
    await user.type(screen.getByLabelText(/Approval request ID/), " 11111111-1111-1111-1111-111111111111 ");
    await user.click(screen.getByRole("button", { name: "Register source" }));
    expect(shared.mutate).toHaveBeenCalledWith({
      display_name: "Workday HR",
      jwks: JSON.parse(KEYS),
      approval_request_id: "11111111-1111-1111-1111-111111111111",
    });
  });

  it("refuses a private key, bad JSON and an empty key set before sending", async () => {
    render(<CreateSourceDialog isOpen onClose={() => undefined} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/Display name/), "HR");
    const field = screen.getByLabelText(/Public keys/);
    const send = () => (screen.getByRole("button", { name: "Register source" }) as HTMLButtonElement).disabled;
    expect(send()).toBe(true);
    fireEvent.change(field, { target: { value: "{nope" } });
    expect(screen.getByText(/not valid JSON/)).toBeTruthy();
    expect(send()).toBe(true);
    fireEvent.change(field, { target: { value: JSON.stringify({ keys: [{ kty: "EC", kid: "k", d: "secret" }] }) } });
    expect(screen.getByText(/private key/)).toBeTruthy();
    expect(send()).toBe(true);
    fireEvent.change(field, { target: { value: KEYS } });
    expect(send()).toBe(false);
  });

  it("sends only the client id when an existing client is chosen", async () => {
    render(<CreateSourceDialog isOpen onClose={() => undefined} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/Display name/), "HR");
    await pickExisting(user);
    await user.type(screen.getByLabelText(/OAuth client ID/), " c1 ");
    await user.click(screen.getByRole("button", { name: "Register source" }));
    expect(shared.mutate).toHaveBeenCalledWith({ display_name: "HR", client_id: "c1", approval_request_id: null });
  });

  it("refuses values wider than the cell's columns before sending", async () => {
    render(<CreateSourceDialog isOpen onClose={() => undefined} />);
    const user = userEvent.setup();
    await pickExisting(user);
    fireEvent.change(screen.getByLabelText(/Display name/), { target: { value: "n".repeat(NAME_MAX + 1) } });
    fireEvent.change(screen.getByLabelText(/OAuth client ID/), { target: { value: "c".repeat(CLIENT_MAX + 1) } });
    expect(screen.getByText(`Use at most ${NAME_MAX} characters.`)).toBeTruthy();
    expect(screen.getByText(`Use at most ${CLIENT_MAX} characters.`)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Register source" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("makes a key pair in the browser, fills the public keys and sends only those", async () => {
    render(<CreateSourceDialog isOpen onClose={() => undefined} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/Display name/), "HR");
    await user.click(screen.getByRole("button", { name: "Make a key pair for me" }));
    expect((screen.getByLabelText(/Public keys/) as HTMLInputElement).value).toBe(made.publicKeys);
    expect(screen.getByText("Save the private key now")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Register source" }));
    const sent = JSON.stringify(shared.mutate.mock.calls[0]?.[0]);
    expect(sent).toContain("scim-made1");
    expect(sent).not.toContain("SECRET-D"); // the private half never leaves the browser
    await user.click(screen.getByRole("button", { name: "Download private key" }));
    expect(made.download).toHaveBeenCalledWith("HR-private-key.json", made.privateKey);
  });

  it("drops the private key notice once the keys box no longer holds the key that was made", async () => {
    render(<CreateSourceDialog isOpen onClose={() => undefined} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Make a key pair for me" }));
    expect(screen.getByText("Save the private key now")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/Public keys/), { target: { value: KEYS } });
    expect(screen.queryByText("Save the private key now")).toBeNull();
  });

  it("keeps the private key on screen after the source is registered, so it can still be saved", async () => {
    render(<CreateSourceDialog isOpen onClose={() => undefined} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Make a key pair for me" }));
    act(() =>
      shared.onSuccess?.({
        source_id: "s-1",
        client_id: "hr-1",
        display_name: "HR",
        status: "ACTIVE",
        client_created: true,
        scim_base_url: "https://id.example/scim/v2",
        token_endpoint: "https://id.example/oauth2/token",
      }),
    );
    expect(screen.getByText("HR is active")).toBeTruthy();
    expect(screen.getByText("Save the private key now")).toBeTruthy();
  });

  it("explains a plan without SCIM instead of a bare code", () => {
    shared.error = new ApiError({ status: 402, code: "ENTITLEMENT_REQUIRED", message: "Your plan does not include this." });
    render(<CreateSourceDialog isOpen onClose={() => undefined} />);
    expect(screen.getByText("Not included in your plan")).toBeTruthy();
    expect(screen.queryByText("generic error")).toBeNull();
  });

  it("leaves step-up, approval and other errors to the shared view", () => {
    shared.error = new ApiError({ status: 422, code: "PROVISIONING_CLIENT_INVALID", message: "must exist" });
    render(<CreateSourceDialog isOpen onClose={() => undefined} />);
    expect(screen.getByText("generic error")).toBeTruthy();
  });

  it("shows the registered source's name from the response", () => {
    render(<CreateSourceDialog isOpen onClose={() => undefined} />);
    act(() =>
      shared.onSuccess?.({
        source_id: "s-1",
        client_id: "workday-hr-3f9a21c4",
        display_name: "Workday HR",
        status: "ACTIVE",
        client_created: true,
        scim_base_url: "https://id.example/scim/v2",
        token_endpoint: "https://id.example/oauth2/token",
      }),
    );
    expect(screen.getByText("Workday HR is active")).toBeTruthy();
  });
});
