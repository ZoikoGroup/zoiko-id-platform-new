/** The approval queue shows a reviewer what they need to decide, and warns where it is missing; the pipeline guide is the recommended way in. */
import type { BuildItem } from "@zoiko/api-client";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@zoiko/ui", () => ({
  Button: ({ children, onPress }: { children: ReactNode; onPress?: () => void }) => <button onClick={onPress}>{children}</button>,
  Card: ({ title, children }: { title?: ReactNode; children?: ReactNode }) => (
    <section>
      <h2>{title}</h2>
      {children}
    </section>
  ),
  CopyId: ({ value }: { value: string }) => <span>{value}</span>,
  DateTime: ({ value }: { value: string }) => <time>{value}</time>,
}));
vi.mock("../team/person-label", () => ({ PersonLabel: ({ fallbackId }: { fallbackId?: string }) => <span>{fallbackId}</span> }));
vi.mock("./pipeline-credentials", () => ({ PipelineCredentialsDialog: ({ isOpen }: { isOpen: boolean }) => (isOpen ? <div>credentials dialog is open</div> : null) }));

import { ApprovalQueue, PipelineGuide } from "./build-review";

afterEach(cleanup);

const digest = (c: string) => `sha256:${c.repeat(64)}`;
const build = (over: Partial<BuildItem>): BuildItem => ({
  build_id: "b1",
  name: "Invoice bot 1.4",
  artifact_digest: digest("a"),
  source_ref: "ghcr.io/acme/invoice-bot:1.4",
  provenance_ref: "https://ci.example.com/run/1",
  registered_by: "pipeline-1",
  review_status: "DRAFT",
  approved_by: null,
  registered_at: "2026-11-09T10:00:00Z",
  approved_at: null,
  revoked_at: null,
  revoke_reason: null,
  ...over,
});

describe("Waiting for approval", () => {
  it("shows nothing when nothing waits", () => {
    const { container } = render(<ApprovalQueue builds={[build({ review_status: "APPROVED" })]} person={() => undefined} approve={() => undefined} />);
    expect(container.textContent).toBe("");
  });

  it("lists only what waits, with what a reviewer needs: the digest, the source, the build record and who registered it", () => {
    render(
      <ApprovalQueue
        builds={[build({}), build({ build_id: "b2", name: "Already approved", review_status: "APPROVED" })]}
        person={() => undefined}
        approve={() => undefined}
      />,
    );
    expect(screen.getByText("Waiting for approval (1)")).toBeTruthy();
    expect(screen.queryByText("Already approved")).toBeNull();
    expect(screen.getByText(digest("a"))).toBeTruthy();
    expect(screen.getByText(/ghcr.io\/acme\/invoice-bot:1.4/)).toBeTruthy();
    expect(screen.getByText("pipeline-1")).toBeTruthy();
    const link = screen.getByRole("link", { name: /How it was built/ }) as HTMLAnchorElement;
    expect(link.href).toBe("https://ci.example.com/run/1");
    expect(link.rel).toContain("noreferrer");
  });

  it("warns plainly when no source or proof of the build was recorded", () => {
    render(<ApprovalQueue builds={[build({ source_ref: null, provenance_ref: null })]} person={() => undefined} approve={() => undefined} />);
    expect(screen.getByText(/No source was recorded/)).toBeTruthy();
    expect(screen.getByText(/No proof of how it was built was recorded/)).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("never turns a proof that is not an https address into a link", () => {
    render(<ApprovalQueue builds={[build({ provenance_ref: "javascript:alert(1)" })]} person={() => undefined} approve={() => undefined} />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText(/Proof: javascript:alert\(1\)/)).toBeTruthy();
  });

  it("does not offer Approve for a build the signed-in person registered, and says why", () => {
    render(<ApprovalQueue builds={[build({ registered_by: "me-1" }), build({ build_id: "b9", name: "Theirs", registered_by: "pipeline-1" })]} person={() => undefined} approve={() => undefined} me="me-1" />);
    expect(screen.getAllByRole("button", { name: "Approve" })).toHaveLength(1); // only the one someone else registered
    expect(screen.getByText(/You registered this build, so someone else has to approve it/)).toBeTruthy();
  });

  it("approves the build that was chosen", async () => {
    const approve = vi.fn();
    render(<ApprovalQueue builds={[build({}), build({ build_id: "b3", name: "Second" })]} person={() => undefined} approve={approve} />);
    await userEvent.setup().click(screen.getAllByRole("button", { name: "Approve" })[1] as HTMLElement);
    expect(approve).toHaveBeenCalledTimes(1);
    expect(approve.mock.calls[0]?.[0]).toMatchObject({ build_id: "b3" });
  });
});

describe("Let your build pipeline register builds", () => {
  it("is open until a first build exists, then folded away but still there", () => {
    const { container, rerender } = render(<PipelineGuide hasBuilds={false} />);
    expect((container.querySelector("details") as HTMLDetailsElement).open).toBe(true);
    rerender(<PipelineGuide hasBuilds />);
    expect((container.querySelector("details") as HTMLDetailsElement).open).toBe(false);
    expect(screen.getByText(/Let your build pipeline register builds/)).toBeTruthy();
  });

  it("shows this environment's real API address in the command, and a placeholder only when it is not known", () => {
    const real = render(<PipelineGuide hasBuilds={false} apiBase="https://api.acme.example" />);
    expect(real.container.textContent).toContain("--api-base https://api.acme.example");
    expect(real.container.textContent).not.toContain("<your domain>");
    real.unmount();
    expect(render(<PipelineGuide hasBuilds={false} />).container.textContent).toContain("https://api.<your domain>");
  });

  it("makes the pipeline's credentials from the guide in one press", async () => {
    render(<PipelineGuide hasBuilds={false} />);
    expect(screen.queryByText("credentials dialog is open")).toBeNull();
    await userEvent.setup().click(screen.getByRole("button", { name: "Create pipeline credentials" }));
    expect(screen.getByText("credentials dialog is open")).toBeTruthy();
  });

  it("gives the grant it needs, the script, and a command with no digest to type", () => {
    const { container } = render(<PipelineGuide hasBuilds={false} />);
    const text = container.textContent ?? "";
    expect(text).toContain("Register a software build");
    expect(text).toContain("tools/ci/register-agent-build.yml");
    expect(text).toContain("register_build.py");
    expect(text).toContain("never typed by hand");
    expect(text).toContain("cannot approve");
  });
});
