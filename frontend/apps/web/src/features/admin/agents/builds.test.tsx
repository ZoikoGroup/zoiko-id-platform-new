/** Registering a build takes one field, the image: the digest and the name are worked out for the person, and a digest is typed only where lookup is off or fails. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const shared = vi.hoisted(() => ({
  resolve: vi.fn(),
  resolveApi: vi.fn(),
  register: vi.fn(),
  lookupError: null as unknown,
  versions: undefined as undefined | { tags: string[] },
  versionsError: null as unknown,
  onSuccess: undefined as undefined | ((r: unknown) => void),
}));

vi.mock("@/lib/cell", () => ({
  useCellQuery: (key: unknown[]) => ({ data: key[0] === "build-versions" ? shared.versions : undefined, isPending: false, error: key[0] === "build-versions" ? shared.versionsError : null }),
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
  useRuntime: () => ({ api: { agents: { registerBuild: shared.register, resolveBuild: shared.resolveApi } }, protect: (f: () => unknown) => f() }),
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
  Select: ({ label, value, onChange, options, placeholder, isDisabled }: { label: string; value?: string | null; onChange?: (v: string) => void; options: { id: string; label: string }[]; placeholder?: string; isDisabled?: boolean }) => (
    <label>
      {label}
      <select aria-label={label} value={value ?? ""} disabled={isDisabled} onChange={(e) => onChange?.(e.target.value)}>
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  ),
  TextField: ({ label, value, onChange, errorMessage }: { label: string; value: string; onChange: (v: string) => void; errorMessage?: string }) => (
    <label>
      {label}
      <input aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} />
      {errorMessage ? <span>{errorMessage}</span> : null}
    </label>
  ),
  useToast: () => ({ notify: () => undefined }),
}));

import { RegisterBuildDialog } from "./builds";

beforeEach(() => {
  shared.resolve.mockReset();
  shared.resolveApi.mockReset();
  shared.register.mockReset();
  shared.lookupError = null;
  shared.versions = undefined;
  shared.versionsError = null;
});
afterEach(cleanup);

describe("Register build", () => {
  const found = `sha256:${"c".repeat(64)}`;
  const pinned = `sha256:${"a".repeat(64)}`;

  it("where lookup is off, still needs the digest and says where it comes from", () => {
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled={false} />);
    expect(screen.queryByText("Look up digest")).toBeNull();
    expect(screen.getByText("digest help")).toBeTruthy();
    expect((screen.getByLabelText("Build digest") as HTMLInputElement).value).toBe("");
  });

  it("where lookup is on, asks for the image and nothing else: no digest field, no source field", () => {
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled />);
    expect(screen.getByLabelText("Image")).toBeTruthy();
    expect(screen.queryByLabelText("Build digest")).toBeNull();
    expect(screen.queryByLabelText("Where it comes from (optional)")).toBeNull();
  });

  it("looks the digest up on its own once the image is typed, and suggests the name", async () => {
    const user = userEvent.setup();
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled />);
    await user.type(screen.getByLabelText("Image"), "ghcr.io/acme/bot:1.4");
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Bot 1.4");
    await waitFor(() => expect(shared.resolve).toHaveBeenCalledWith("ghcr.io/acme/bot:1.4"), { timeout: 3000 });
    expect(shared.resolve).toHaveBeenCalledTimes(1); // once, for the finished name, not for every key
    expect(await screen.findByText(found)).toBeTruthy();
    expect(screen.getByText(/A tag can move/)).toBeTruthy();
  });

  it("keeps a name the person chose when the image changes", async () => {
    const user = userEvent.setup();
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled />);
    await user.type(screen.getByLabelText("Name"), "Payments agent");
    await user.type(screen.getByLabelText("Image"), "ghcr.io/acme/bot:1.4");
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Payments agent");
  });

  it("registers from the image alone without waiting for the lookup: the digest is looked up then", async () => {
    const user = userEvent.setup();
    shared.resolveApi.mockResolvedValue({ registry: "ghcr.io", repository: "acme/bot", reference: "1.4", digest: found, image: "x", media_type: "x" });
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled />);
    await user.type(screen.getByLabelText("Image"), "ghcr.io/acme/bot:1.4");
    await user.click(screen.getByRole("button", { name: "Register build" }));
    expect(shared.resolveApi).toHaveBeenCalledWith("ghcr.io/acme/bot:1.4");
    expect(shared.register).toHaveBeenCalledWith({ name: "Bot 1.4", artifact_digest: found, source_ref: "ghcr.io/acme/bot:1.4", provenance_ref: null });
  });

  it("needs no lookup for a name that already carries its digest, as docker inspect prints it", async () => {
    const user = userEvent.setup();
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled />);
    await user.type(screen.getByLabelText("Image"), `ghcr.io/acme/bot@${pinned}`);
    expect(screen.getByText(/Digest taken from the name/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Register build" }));
    expect(shared.resolveApi).not.toHaveBeenCalled();
    expect(shared.register).toHaveBeenCalledWith({ name: "Bot", artifact_digest: pinned, source_ref: "ghcr.io/acme/bot", provenance_ref: null });
  });

  it("shows why a lookup failed, and offers the digest field as the way forward", async () => {
    shared.lookupError = { error: { code: "REGISTRY_NOT_ALLOWED" } };
    const user = userEvent.setup();
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled />);
    await user.type(screen.getByLabelText("Image"), "evil.example.com/x:1");
    expect(screen.getByRole("alert").textContent).toContain("could not be looked up");
    expect((screen.getByLabelText("Build digest") as HTMLInputElement).value).toBe("");
  });

  it("says as it is typed that a plain word is not an image name, with what one looks like, and asks no registry about it", async () => {
    const user = userEvent.setup();
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled />);
    await user.type(screen.getByLabelText("Image"), "finance");
    expect(screen.getByText(/That is not an image name/)).toBeTruthy();
    expect(shared.resolve).not.toHaveBeenCalled();
  });

  it("does not call a real image name wrong", async () => {
    const user = userEvent.setup();
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled />);
    await user.type(screen.getByLabelText("Image"), "acme/invoice-agent:1.0.0");
    expect(screen.queryByText(/That is not an image name/)).toBeNull();
  });

  it("still registers a typed digest where lookup is off", async () => {
    const user = userEvent.setup();
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled={false} />);
    await user.type(screen.getByLabelText("Image"), "ghcr.io/acme/bot:1.4");
    await user.type(screen.getByLabelText("Build digest"), pinned);
    await user.click(screen.getByRole("button", { name: "Register build" }));
    expect(shared.register).toHaveBeenCalledWith({ name: "Bot 1.4", artifact_digest: pinned, source_ref: "ghcr.io/acme/bot:1.4", provenance_ref: null });
  });

  it("asks for the image when nothing was given, and registers nothing", async () => {
    const user = userEvent.setup();
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled />);
    await user.click(screen.getByRole("button", { name: "Register build" }));
    expect(screen.getByText(/Paste the image name, for example/)).toBeTruthy();
    expect(shared.register).not.toHaveBeenCalled();
  });

  it("lets a person who has a digest enter it by hand instead", async () => {
    const user = userEvent.setup();
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled />);
    await user.click(screen.getByRole("button", { name: "Enter a digest by hand instead" }));
    expect(screen.getByLabelText("Build digest")).toBeTruthy();
  });
});

describe("Register build, picking instead of typing", () => {
  const repos = ["ghcr.io/acme/bot", "ghcr.io/acme/other"];
  const found = `sha256:${"c".repeat(64)}`;
  const options = (label: string) => Array.from((screen.getByLabelText(label) as HTMLSelectElement).options).map((o) => o.value).filter(Boolean);

  it("offers the known software to pick, with no image to type", () => {
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled repositories={repos} />);
    expect(screen.queryByLabelText("Image")).toBeNull();
    expect(options("Software")).toEqual([...repos, "__other__"]);
    expect(screen.queryByLabelText("Version")).toBeNull(); // nothing to choose a version of yet
  });

  it("lists the versions newest first, then looks the digest up for the one chosen and suggests the name", async () => {
    const user = userEvent.setup();
    shared.versions = { tags: ["1.3", "latest", "1.10"] };
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled repositories={repos} />);
    await user.selectOptions(screen.getByLabelText("Software"), "ghcr.io/acme/bot");
    expect(options("Version")).toEqual(["1.10", "1.3", "latest"]);
    await user.selectOptions(screen.getByLabelText("Version"), "1.10");
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Bot 1.10");
    await waitFor(() => expect(shared.resolve).toHaveBeenCalledWith("ghcr.io/acme/bot:1.10"), { timeout: 3000 });
    expect(await screen.findByText(found)).toBeTruthy();
  });

  it("has nothing to choose when only one piece of software is known: its versions are offered at once", () => {
    shared.versions = { tags: ["2.0.0"] };
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled repositories={[repos[0] ?? ""]} />);
    expect((screen.getByLabelText("Software") as HTMLSelectElement).value).toBe("ghcr.io/acme/bot");
    expect(options("Version")).toEqual(["2.0.0"]);
  });

  it("registers what was picked, with no typing at all", async () => {
    const user = userEvent.setup();
    shared.versions = { tags: ["1.4"] };
    shared.resolveApi.mockResolvedValue({ registry: "ghcr.io", repository: "acme/bot", reference: "1.4", digest: found, image: "x", media_type: "x" });
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled repositories={repos} />);
    await user.selectOptions(screen.getByLabelText("Software"), "ghcr.io/acme/bot");
    await user.selectOptions(screen.getByLabelText("Version"), "1.4");
    await user.click(screen.getByRole("button", { name: "Register build" }));
    expect(shared.register).toHaveBeenCalledWith({ name: "Bot 1.4", artifact_digest: found, source_ref: "ghcr.io/acme/bot:1.4", provenance_ref: null });
  });

  it("asks which software and version when nothing was chosen, and registers nothing", async () => {
    const user = userEvent.setup();
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled repositories={repos} />);
    await user.click(screen.getByRole("button", { name: "Register build" }));
    expect(screen.getByText(/Choose the software and its version/)).toBeTruthy();
    expect(shared.register).not.toHaveBeenCalled();
  });

  it("falls back to typing for software that is not listed yet, and back again", async () => {
    const user = userEvent.setup();
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled repositories={repos} />);
    await user.selectOptions(screen.getByLabelText("Software"), "__other__");
    expect(screen.getByLabelText("Image")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Pick from a list instead" }));
    expect(screen.queryByLabelText("Image")).toBeNull();
    expect(screen.getByLabelText("Software")).toBeTruthy();
  });

  it("says why there is nothing to pick yet, and how to avoid typing, where no software is known", () => {
    const { unmount } = render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled repositories={[]} />);
    expect(screen.getByText(/No software is listed yet/)).toBeTruthy();
    expect(screen.getByText(/ZID_BUILD_IMAGES/)).toBeTruthy();
    unmount();
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled repositories={repos} />);
    expect(screen.queryByText(/No software is listed yet/)).toBeNull();
  });

  it("types as before where nothing is known and where lookup is off", () => {
    const { unmount } = render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled repositories={[]} />);
    expect(screen.getByLabelText("Image")).toBeTruthy();
    unmount();
    render(<RegisterBuildDialog isOpen onClose={() => undefined} lookupEnabled={false} repositories={repos} />);
    expect(screen.getByLabelText("Image")).toBeTruthy();
    expect(screen.queryByLabelText("Software")).toBeNull();
  });
});
