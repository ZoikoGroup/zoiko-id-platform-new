/** Registering a build: the digest can be looked up from an image name where an operator has switched that on, and is typed once where not. */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const shared = vi.hoisted(() => ({
  resolve: vi.fn(),
  register: vi.fn(),
  lookupError: null as unknown,
  onSuccess: undefined as undefined | ((r: unknown) => void),
}));

vi.mock("@/lib/cell", () => ({
  useCellQuery: () => ({ data: undefined }),
  QueryView: ({ children }: { children: (d: unknown) => ReactNode }) => <>{children({ items: [], enforced: true, lookup_enabled: true })}</>,
  ApiErrorView: ({ title }: { title?: string }) => <div role="alert">{title ?? "error"}</div>,
  useCellMutation: (fn: unknown, opts: { onSuccess?: (r: unknown) => void }) => {
    shared.onSuccess = opts?.onSuccess;
    return {
      mutate: (v: string) => {
        shared.resolve(v);
        if (!shared.lookupError) opts?.onSuccess?.({ registry: "ghcr.io", repository: "acme/bot", reference: "1.4", digest: `sha256:${"c".repeat(64)}`, image: v, media_type: "x" });
      },
      isPending: false,
      error: shared.lookupError,
      variables: undefined,
      reset: () => undefined,
    };
  },
}));
vi.mock("@/lib/runtime", () => ({
  useRuntime: () => ({ api: { agents: { registerBuild: shared.register } }, protect: (f: () => unknown) => f() }),
}));
vi.mock("@tanstack/react-query", () => ({ useQueryClient: () => ({ invalidateQueries: () => Promise.resolve() }) }));
vi.mock("../team/person-label", () => ({ PersonLabel: () => null, usePeople: () => () => undefined }));
vi.mock("./pickers", () => ({ AuthorityNotice: () => null, DigestHelp: () => <div>digest help</div> }));
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
  Card: ({ children }: { children: ReactNode }) => <section>{children}</section>,
  CopyId: () => null,
  DataTable: () => null,
  DateTime: () => null,
  Dialog: ({ isOpen, children, footer }: { isOpen: boolean; children: ReactNode; footer: ReactNode }) => (isOpen ? <div role="dialog">{children}{footer}</div> : null),
  EmptyState: () => null,
  StatusBadge: () => null,
  TextField: ({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) => (
    <label>
      {label}
      <input aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  ),
  useToast: () => ({ notify: () => undefined }),
}));

import { RegisterBuildDialog } from "./builds";

beforeEach(() => {
  shared.resolve.mockReset();
  shared.register.mockReset();
  shared.lookupError = null;
});
afterEach(cleanup);

describe("Register build", () => {
  it("offers no lookup where no registries are named, and says where the digest comes from", () => {
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled={false} />);
    expect(screen.queryByText("Look up digest")).toBeNull();
    expect(screen.getByText("digest help")).toBeTruthy();
    expect((screen.getByLabelText("Build digest") as HTMLInputElement).value).toBe("");
  });

  it("looks a digest up from the image name and fills it in, with the source, saying a tag can move", async () => {
    const user = userEvent.setup();
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled />);
    const lookup = screen.getByRole("button", { name: "Look up digest" }) as HTMLButtonElement;
    expect(lookup.disabled).toBe(true); // nothing to look up yet
    await user.type(screen.getByLabelText("Image name (optional)"), "ghcr.io/acme/bot:1.4");
    await user.click(lookup);
    expect(shared.resolve).toHaveBeenCalledWith("ghcr.io/acme/bot:1.4");
    expect((screen.getByLabelText("Build digest") as HTMLInputElement).value).toBe(`sha256:${"c".repeat(64)}`);
    expect((screen.getByLabelText("Where it comes from (optional)") as HTMLInputElement).value).toBe("ghcr.io/acme/bot:1.4");
    expect(screen.getByText(/A tag can move/)).toBeTruthy();
  });

  it("shows why a lookup failed and leaves the digest alone", async () => {
    shared.lookupError = { error: { code: "REGISTRY_NOT_ALLOWED" } };
    const user = userEvent.setup();
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled />);
    await user.type(screen.getByLabelText("Image name (optional)"), "evil.example.com/x:1");
    await user.click(screen.getByRole("button", { name: "Look up digest" }));
    expect(screen.getByRole("alert").textContent).toContain("could not be looked up");
    expect((screen.getByLabelText("Build digest") as HTMLInputElement).value).toBe("");
  });

  it("still registers exactly what is in the digest field, whether it was looked up or typed", async () => {
    const user = userEvent.setup();
    const d = `sha256:${"a".repeat(64)}`;
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled={false} />);
    await user.type(screen.getByLabelText("Name"), "Invoice bot 1.4");
    await user.type(screen.getByLabelText("Build digest"), d);
    await user.click(screen.getByRole("button", { name: "Register build" }));
    expect(shared.register).toHaveBeenCalledWith({ name: "Invoice bot 1.4", artifact_digest: d, source_ref: null, provenance_ref: null });
  });
});
