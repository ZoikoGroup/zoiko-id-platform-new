/**
 * CR-141: what the Register an API client form sends. Whatever shape of public key the person pastes, the API gets one checked key set;
 * a private key is refused before any call is made, and the guide for getting a key is on the form.
 * Only the UI shell and the network are stood in for; the key conversion is the real one, on real keys.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { webcrypto } from "node:crypto";
import type { ReactNode } from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const shared = vi.hoisted(() => ({ register: vi.fn() }));

vi.mock("@/lib/cell", () => ({
  useCellQuery: () => ({ data: undefined }),
  ApiErrorView: () => <div role="alert">error</div>,
  useCellMutation: (fn: (api: unknown) => unknown, opts: { onSuccess?: (r: unknown) => void }) => ({
    mutate: () => {
      void Promise.resolve(fn({ agents: { registerApiClient: shared.register } })).then((r) => opts?.onSuccess?.(r));
    },
    isPending: false,
    error: null,
    reset: () => undefined,
  }),
}));
vi.mock("@zoiko/ui", () => ({
  Badge: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  Banner: ({ title, children }: { title: string; children?: ReactNode }) => (
    <div role="status">
      {title}
      {children}
    </div>
  ),
  Button: ({ children, onPress }: { children: ReactNode; onPress?: () => void }) => <button onClick={onPress}>{children}</button>,
  Card: ({ children }: { children: ReactNode }) => <section>{children}</section>,
  Dialog: ({ isOpen, children, footer }: { isOpen: boolean; children: ReactNode; footer: ReactNode }) => (isOpen ? <div role="dialog">{children}{footer}</div> : null),
  KeyValues: () => null,
  TextField: ({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) => (
    <label>
      {label}
      <textarea aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  ),
}));

import { RegisterApiClientDialog } from "./api-agent";

beforeAll(() => {
  vi.stubGlobal("crypto", webcrypto);
});
beforeEach(() => {
  shared.register.mockReset();
  shared.register.mockResolvedValue({ client_id: "c1", build_id: "b1", artifact_digest: `sha256:${"a".repeat(64)}`, thumbprints: ["t"], next: "A second person approves it." });
});
afterEach(cleanup);

const pem = async () => {
  const pair = (await webcrypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"])) as CryptoKeyPair;
  const wrap = (label: string, der: ArrayBuffer) => `-----BEGIN ${label}-----\n${Buffer.from(der).toString("base64")}\n-----END ${label}-----\n`;
  return { pub: wrap("PUBLIC KEY", await webcrypto.subtle.exportKey("spki", pair.publicKey)), priv: wrap("PRIVATE KEY", await webcrypto.subtle.exportKey("pkcs8", pair.privateKey)) };
};

// userEvent.type treats braces and brackets as special; paste puts the text in whole, as a person would
async function fill(label: string, text: string) {
  const u = userEvent.setup();
  await u.click(screen.getByLabelText(label));
  await u.paste(text);
}

async function open(name: string, audience: string, key: string) {
  render(<RegisterApiClientDialog isOpen onClose={() => undefined} />);
  await fill("Client name", name);
  await fill("Audiences", audience);
  await fill("Public key", key);
  await userEvent.setup().click(screen.getByText("Register client"));
}

describe("Register an API client", () => {
  it("tells the person how to get a key, safe way first, and never asks for a private one", () => {
    render(<RegisterApiClientDialog isOpen onClose={() => undefined} />);
    expect(screen.getByText("How do I get a public key?")).toBeTruthy();
    expect(screen.getByText(/Best for production: a key service/)).toBeTruthy();
    expect(screen.getByText(/openssl ec -in agent.key -pubout/)).toBeTruthy();
    expect(screen.getByText(/We never see or hold the private key/)).toBeTruthy();
  });

  it("sends the PEM public key file as a named key set", async () => {
    const { pub } = await pem();
    await open("Invoice reader", "http://localhost:8000", pub);
    await waitFor(() => expect(shared.register).toHaveBeenCalledTimes(1));
    const body = shared.register.mock.calls[0]![0] as { name: string; audiences: string[]; jwks: { keys: Record<string, string>[] } };
    expect(body.name).toBe("Invoice reader");
    expect(body.audiences).toEqual(["http://localhost:8000"]);
    expect(body.jwks.keys).toHaveLength(1);
    expect(body.jwks.keys[0]).toMatchObject({ kty: "EC", crv: "P-256" });
    expect(body.jwks.keys[0]!.kid).toBeTruthy();
    expect(Object.keys(body.jwks.keys[0]!).some((m) => ["d", "p", "q", "k"].includes(m))).toBe(false);
  });

  it("refuses a private key and makes no call", async () => {
    const { priv } = await pem();
    await open("Invoice reader", "http://localhost:8000", priv);
    await waitFor(() => expect(screen.getByText(/private material/)).toBeTruthy());
    expect(shared.register).not.toHaveBeenCalled();
    expect(screen.queryByText(priv.split("\n")[1]!)).toBeNull(); // and does not echo it back
  });

  it("asks for a name and an audience before it looks at the key", async () => {
    render(<RegisterApiClientDialog isOpen onClose={() => undefined} />);
    await userEvent.setup().click(screen.getByText("Register client"));
    expect(screen.getByText(/Give the client a name\./)).toBeTruthy();
    expect(shared.register).not.toHaveBeenCalled();
  });
});
