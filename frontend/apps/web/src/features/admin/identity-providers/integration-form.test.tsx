/** Create-integration form: a SAML provider is sent with the suggested entity id when none is typed, ACR levels are checked, and the ACS address is shown after create. */
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const shared = vi.hoisted(() => ({
  mutate: vi.fn(),
  onSuccess: undefined as undefined | ((r: unknown) => void),
}));

vi.mock("@/lib/cell", () => ({
  useCellQuery: (key: string[]) => ({
    data: key.includes("connection")
      ? { api_base: "https://api.test", token_url: "https://api.test/oauth2/token" }
      : { items: [] },
    isPending: false,
  }),
  useCellMutation: (_fn: unknown, opts: { invalidate?: unknown; onSuccess?: (r: unknown) => void }) => {
    if (opts.invalidate) shared.onSuccess = opts.onSuccess; // the create mutation; discovery has none
    return { mutate: shared.mutate, isPending: false, isError: false, error: null, reset: () => undefined };
  },
  jsonInput: (v: string) => (v.trim() ? JSON.parse(v) : undefined),
  listInput: (v: string) => v.split(/[,\n]/).map((s) => s.trim()).filter(Boolean),
}));

vi.mock("@zoiko/ui", () => ({
  Banner: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Button: ({ children, onPress }: { children: ReactNode; onPress?: () => void }) => <button onClick={onPress}>{children}</button>,
  Checkbox: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  CopyId: ({ value, label }: { value: string; label: string }) => <span data-label={label}>{value}</span>,
  Dialog: ({ title, children, footer }: { title: string; children: ReactNode; footer: ReactNode }) => (
    <div>
      <h2>{title}</h2>
      {children}
      {footer}
    </div>
  ),
  RadioCards: ({ options, onChange }: { options: { id: string; label: string }[]; onChange: (v: string) => void }) => (
    <div>
      {options.map((o) => (
        <button key={o.id} onClick={() => onChange(o.id)}>
          {o.label}
        </button>
      ))}
    </div>
  ),
  Select: () => null,
  TextField: ({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) => (
    <label>
      {label}
      <input value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  ),
}));

vi.mock("./shared", async () => ({
  ...(await vi.importActual<object>("./shared")),
  AlgorithmPicker: () => null,
  FormDialog: ({ children, onSubmit, error }: { children: ReactNode; onSubmit: () => void; error: unknown }) => (
    <div>
      {children}
      {error instanceof Error ? <p role="alert">{error.message}</p> : null}
      <button onClick={onSubmit}>Connect provider</button>
    </div>
  ),
}));

import { CreateIntegrationDialog } from "./integration-form";

afterEach(() => {
  cleanup();
  shared.mutate.mockReset();
});

async function fillSaml() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "A SAML 2.0 provider" }));
  await user.type(screen.getByLabelText("Name people will see"), "Acme ADFS");
  await user.type(screen.getByLabelText("The provider's entity id"), "https://idp.acme.example/saml");
  await user.type(screen.getByLabelText("Single sign-on address"), "https://idp.acme.example/sso");
  await user.type(screen.getByLabelText("Signing certificates"), "MIIBcert");
  return user;
}

describe("CreateIntegrationDialog", () => {
  it("sends the suggested SP entity id for a SAML provider when none is typed", async () => {
    render(<CreateIntegrationDialog onClose={() => undefined} />);
    const user = await fillSaml();
    await user.click(screen.getByRole("button", { name: "Connect provider" }));
    expect(shared.mutate).toHaveBeenCalledTimes(1);
    expect(shared.mutate.mock.calls[0]![0]).toMatchObject({
      protocol: "SAML",
      display_name: "Acme ADFS",
      issuer: "https://idp.acme.example/saml",
      sso_url: "https://idp.acme.example/sso",
      idp_certificates: ["MIIBcert"],
      sp_entity_id: "https://api.test/saml/sp",
      allow_idp_initiated: false,
    });
  });

  it("refuses an ACR level that is not a whole number of 0 or more", async () => {
    render(<CreateIntegrationDialog onClose={() => undefined} />);
    const user = await fillSaml();
    await user.type(screen.getByLabelText("Assurance levels (optional)"), '{{"urn:x": -1}');
    await user.click(screen.getByRole("button", { name: "Connect provider" }));
    expect(shared.mutate).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toMatch(/0 or more/);
  });

  it("shows the ACS address and entity id after a SAML provider is created", async () => {
    render(<CreateIntegrationDialog onClose={() => undefined} />);
    await fillSaml();
    act(() => shared.onSuccess?.({ acs_url: "https://id.test/federation/saml/acs" }));
    expect(screen.getByText("https://id.test/federation/saml/acs")).toBeTruthy();
    expect(screen.getByText("https://api.test/saml/sp")).toBeTruthy();
  });
});
