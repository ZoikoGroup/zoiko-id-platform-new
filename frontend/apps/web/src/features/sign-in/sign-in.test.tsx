import { ApiError } from "@zoiko/api-client";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const flow = { passkeyOptions: vi.fn(), passkeyVerify: vi.fn() };
vi.mock("@/lib/runtime", () => ({ useRuntime: () => ({ flow }) }));
vi.mock("@/lib/webauthn", async (orig) => ({
  ...(await orig<typeof import("@/lib/webauthn")>()),
  browserSupportsWebAuthn: () => true,
  getPasskey: vi.fn(),
}));

import { SignIn } from "@/features/sign-in/sign-in";

function unknownClient(): ApiError {
  return new ApiError({ status: 400, code: "CLIENT_UNKNOWN", message: "Unknown client." } as never);
}

describe("sign-in with a saved workspace this server does not know", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("forgets the saved workspace and asks for it again instead of failing the same way every time", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    flow.passkeyOptions.mockRejectedValueOnce(unknownClient());

    render(<SignIn connections={[]} workspace="Acme" returnTo="/overview" />);
    fireEvent.click(screen.getByRole("button", { name: /passkey/i }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/sign-in?error=workspace_unknown&returnTo=%2Foverview"));
    expect(fetchSpy).toHaveBeenCalledWith("/api/workspace", expect.objectContaining({ method: "DELETE" }));
  });

  it("explains the forgotten workspace when the page comes back", () => {
    render(<SignIn connections={[]} workspace={null} error="workspace_unknown" />);
    expect(screen.getByText(/remembered a workspace this server does not have/i)).toBeTruthy();
  });
});

describe("a passkey refused on the default workspace", () => {
  afterEach(cleanup);
  it("points to Change instead of only saying it failed", async () => {
    vi.stubGlobal("location", { ...window.location, assign: vi.fn() });
    flow.passkeyOptions.mockResolvedValueOnce({ challenge: "c" });
    flow.passkeyVerify.mockRejectedValueOnce(
      new ApiError({ status: 401, code: "AUTHENTICATOR_NOT_ACTIVE", message: "Authentication failed." } as never),
    );
    render(<SignIn connections={[]} workspace={null} />);
    fireEvent.click(screen.getByRole("button", { name: /passkey/i }));
    await waitFor(() => expect(screen.getByText(/not registered in the default workspace/i)).toBeTruthy());
  });
});
