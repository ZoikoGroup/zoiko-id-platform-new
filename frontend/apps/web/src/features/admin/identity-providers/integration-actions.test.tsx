/** Edit dialog: prefilled with the integration, sends only editable fields, applies the create form's checks, shows the server's refusal. */
import type { FederationIntegration } from "@zoiko/api-client";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const shared = vi.hoisted(() => ({ mutate: vi.fn(), error: null as unknown }));

vi.mock("@/lib/cell", () => ({
  useCellMutation: () => ({ mutate: shared.mutate, isPending: false, error: shared.error, reset: () => undefined }),
  jsonInput: (v: string) => (v.trim() ? JSON.parse(v) : undefined),
  listInput: (v: string) => v.split(/[,\n]/).map((s) => s.trim()).filter(Boolean),
}));

vi.mock("@zoiko/ui", () => ({
  Banner: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Button: ({ children, onPress }: { children: ReactNode; onPress?: () => void }) => <button onClick={onPress}>{children}</button>,
  Checkbox: ({ children, isSelected, onChange }: { children: ReactNode; isSelected: boolean; onChange: (v: boolean) => void }) => (
    <label>
      <input type="checkbox" checked={isSelected} onChange={(e) => onChange(e.target.checked)} />
      {children}
    </label>
  ),
  Code: ({ children }: { children: ReactNode }) => <code>{children}</code>,
  CopyId: () => null,
  Dialog: () => null,
  Select: () => null,
  Switch: () => null,
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
      <button onClick={onSubmit}>Save changes</button>
    </div>
  ),
}));

import { EditIntegrationDialog } from "./integration-actions";

const base: Omit<FederationIntegration, "protocol"> = {
  connection_id: "11111111-1111-1111-1111-111111111111",
  display_name: "Acme",
  issuer: "https://login.acme.example",
  status: "ACTIVE",
  config_revision: 2,
  metadata_expires_at: "2027-01-01T00:00:00Z",
  link_policy: "EXISTING_ONLY",
  trust_amr: true,
  acr_levels: { phr: 2 },
  algorithms: ["RS256"],
  allow_idp_initiated: false,
  idp_initiated_targets: [],
  require_encrypted_assertions: false,
};
const oidc: FederationIntegration = { ...base, protocol: "OIDC", scopes: ["openid", "email"] };
const saml: FederationIntegration = { ...base, protocol: "SAML", sp_entity_id: "https://sp.example", sp_decryption_key_ref: "secret/k" };

afterEach(() => {
  cleanup();
  shared.mutate.mockReset();
  shared.error = null;
});

describe("EditIntegrationDialog", () => {
  it("prefills and sends the OIDC editable fields only", async () => {
    render(<EditIntegrationDialog integration={oidc} onClose={() => undefined} />);
    expect((screen.getByLabelText("Name people will see") as HTMLInputElement).value).toBe("Acme");
    expect((screen.getByLabelText("Scopes") as HTMLInputElement).value).toBe("openid, email");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Name people will see"), " Entra ");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    const body = shared.mutate.mock.calls[0]![0];
    expect(body).toMatchObject({
      display_name: "Acme Entra",
      link_policy: "EXISTING_ONLY",
      trust_amr: true,
      acr_levels: { phr: 2 },
      algorithms: ["RS256"],
      scopes: ["openid", "email"],
    });
    for (const k of ["issuer", "jwks", "idp_certificates", "sp_entity_id", "require_encrypted_assertions"]) expect(body).not.toHaveProperty(k);
  });

  it("sends the SAML entity id, and encryption only when it changed", async () => {
    render(<EditIntegrationDialog integration={saml} onClose={() => undefined} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    let body = shared.mutate.mock.calls[0]![0];
    expect(body.sp_entity_id).toBe("https://sp.example");
    expect(body).not.toHaveProperty("scopes");
    expect(body).not.toHaveProperty("require_encrypted_assertions");
    await user.click(screen.getByLabelText("Require encrypted assertions"));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    body = shared.mutate.mock.calls[1]![0];
    expect(body.require_encrypted_assertions).toBe(true);
  });

  it("refuses a negative ACR level and a blank name before any request", async () => {
    render(<EditIntegrationDialog integration={oidc} onClose={() => undefined} />);
    const user = userEvent.setup();
    const acr = screen.getByLabelText("Assurance levels (optional)");
    await user.clear(acr);
    await user.type(acr, '{{"x": -1}');
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(screen.getByRole("alert").textContent).toMatch(/0 or more/);
    await user.clear(acr);
    await user.clear(screen.getByLabelText("Name people will see"));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(shared.mutate).not.toHaveBeenCalled();
  });

  it("shows the server's refusal", () => {
    shared.error = new Error("a disabled integration cannot be changed");
    render(<EditIntegrationDialog integration={oidc} onClose={() => undefined} />);
    expect(screen.getByRole("alert").textContent).toMatch(/cannot be changed/);
  });
});
