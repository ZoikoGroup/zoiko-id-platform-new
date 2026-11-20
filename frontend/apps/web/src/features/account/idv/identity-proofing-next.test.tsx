/** The simple identity page: choose Passport or Aadhaar, photograph it, see Verified or Rejected; and it hands the person back to where they came from (`?next=`). */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "@zoiko/ui";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const fake = vi.hoisted(() => ({ idv: { cases: vi.fn(), open: vi.fn(), abandon: vi.fn(), nonce: vi.fn(), documentImage: vi.fn() } }));
const nav = vi.hoisted(() => ({ next: null as string | null, push: vi.fn() }));

vi.mock("@/lib/runtime", () => ({
  useRuntime: () => ({ api: fake, protect: <T,>(run: () => Promise<T>) => run(), session: { authenticated: true, subject: { sub: "u-1" }, csrf: "x" } }),
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () => ({ get: (k: string) => (k === "next" ? nav.next : null) }),
  useRouter: () => ({ push: nav.push }),
}));
vi.mock("./document-capture", () => ({ DocumentCapture: () => <div>camera</div> }));
vi.mock("./live-capture", () => ({ LiveCapture: () => <div>live camera</div> }));

import { IdentityProofingPage } from "./identity-proofing";

// jsdom's File has no arrayBuffer(); every real browser does. A stand-in for the test only.
if (!File.prototype.arrayBuffer) {
  File.prototype.arrayBuffer = function (this: File) {
    return new Promise<ArrayBuffer>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as ArrayBuffer);
      reader.onerror = () => reject(reader.error);
      reader.readAsArrayBuffer(this);
    });
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  nav.next = null;
});

const signals = { optical: "NOT_STARTED", nfc_read: "NOT_RUN", passive_auth: "NOT_RUN", chip_auth: "NOT_RUN", liveness: "NOT_RUN", face_match: "NOT_RUN" };
const base = {
  case_id: "c1",
  market: "GB",
  route: "WEB_FACE",
  state: "OPEN",
  decision: "INDETERMINATE",
  reasons: [] as string[],
  assertions: [],
  signals,
  policy_version: "gb-1",
  policy_hash: "h",
  notice_version: null,
  reviewers: 0,
  attempts: {},
  version: 1,
  created_at: "2026-11-19T08:00:00Z",
  updated_at: "2026-11-19T08:00:00Z",
};
const sent = { ...signals, optical: "DONE" };

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <main>
          <IdentityProofingPage />
        </main>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("simple identity page", () => {
  it("starts with one choice: passport or Aadhaar card", async () => {
    fake.idv.cases.mockResolvedValue({ items: [] });
    mount();
    expect(await screen.findByText("Choose your document")).toBeTruthy();
    expect(screen.getByRole("radio", { name: /Passport/ })).toBeTruthy();
    expect(screen.getByRole("radio", { name: /Aadhaar card/ })).toBeTruthy();
    expect((screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("opens an Aadhaar check for India on the face-match route, with no country to type", async () => {
    fake.idv.cases.mockResolvedValue({ items: [] });
    fake.idv.open.mockResolvedValue({ ...base, route: "WEB_FACE", market: "IN" });
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByRole("radio", { name: /Aadhaar card/ }));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(fake.idv.open).toHaveBeenCalledWith("IN", "WEB_FACE"));
  });

  it("opens a passport check for the typed country on the face-match route", async () => {
    fake.idv.cases.mockResolvedValue({ items: [] });
    fake.idv.open.mockResolvedValue(base);
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByRole("radio", { name: /Passport/ }));
    const go = screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement;
    expect(go.disabled).toBe(true); // the issuing country is needed
    await user.type(screen.getByLabelText(/Country that issued/), "gb");
    await waitFor(() => expect(go.disabled).toBe(false));
    await user.click(go);
    await waitFor(() => expect(fake.idv.open).toHaveBeenCalledWith("GB", "WEB_FACE"));
  });

  it("shows Verified when approved, and Rejected with the reasons when rejected", async () => {
    fake.idv.cases.mockResolvedValue({ items: [{ ...base, decision: "APPROVED", state: "CLOSED", signals: sent }] });
    const first = mount();
    expect(await screen.findByText("Verified")).toBeTruthy();
    first.unmount();
    fake.idv.cases.mockResolvedValue({ items: [{ ...base, decision: "REJECTED", signals: sent, reasons: ["DOCUMENT_EXPIRED"] }] });
    mount();
    expect(await screen.findByText("Rejected")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start again" })).toBeTruthy();
  });

  it("never calls an unreadable photograph a rejection: it asks for it again", async () => {
    fake.idv.cases.mockResolvedValue({ items: [{ ...base, decision: "INDETERMINATE", signals: sent, reasons: ["DOCUMENT_READING_NOT_RELIABLE"] }] });
    mount();
    expect(await screen.findByText("We could not verify this photograph")).toBeTruthy();
    expect(screen.queryByText("Rejected")).toBeNull();
    expect(screen.getByRole("button", { name: "Send for verification" })).toBeTruthy(); // the photograph can be sent again on the same check
  });

  it("shows one card however many earlier attempts there are, the newest, and ignores ended ones", async () => {
    fake.idv.cases.mockResolvedValue({
      items: [
        { ...base, case_id: "old1", created_at: "2026-11-19T07:00:00Z", decision: "ABANDONED", state: "CLOSED" },
        { ...base, case_id: "old2", created_at: "2026-11-19T08:00:00Z", route: "WEB_REVIEW", decision: "REVIEW_REQUIRED", signals: sent },
        { ...base, case_id: "old3", created_at: "2026-11-19T08:30:00Z", route: "WEB_AUTO", decision: "INDETERMINATE" },
        { ...base, case_id: "new", created_at: "2026-11-19T09:00:00Z" },
      ],
    });
    mount();
    expect(await screen.findAllByRole("heading", { name: /verification$/ })).toHaveLength(1);
    expect(screen.getByText("Identity verification")).toBeTruthy(); // the newest face-match check; the older checks on retired routes are not shown
  });

  it("continues to the page that sent the person here once verified, never to another site", async () => {
    nav.next = "/admin/onboarding";
    fake.idv.cases.mockResolvedValue({ items: [{ ...base, decision: "APPROVED", state: "CLOSED", signals: sent }] });
    const first = mount();
    expect(await screen.findByText("Identity verified")).toBeTruthy();
    first.unmount();
    for (const next of [null, "https://evil.example/", "//evil.example"]) {
      nav.next = next;
      const view = mount();
      await screen.findByText("Verify your identity");
      expect(screen.queryByText("Identity verified")).toBeNull();
      view.unmount();
    }
  });

  it("asks for the document photograph, a live photograph and the person's agreement, and sends them together", async () => {
    fake.idv.cases.mockResolvedValue({ items: [{ ...base, route: "WEB_FACE" }] });
    fake.idv.nonce.mockResolvedValue({ nonce: "n".repeat(16) });
    fake.idv.documentImage.mockResolvedValue({ ...base, route: "WEB_FACE", decision: "APPROVED", state: "CLOSED" });
    const user = userEvent.setup();
    mount();
    const send = (await screen.findByRole("button", { name: "Send for verification" })) as HTMLButtonElement;
    expect(screen.getByText(/Step 1/)).toBeTruthy();
    expect(screen.getByText(/Step 2/)).toBeTruthy();
    expect(send.disabled).toBe(true); // nothing chosen yet
    const jpeg = (name: string) => new File([new Uint8Array(30 * 1024)], name, { type: "image/jpeg" });
    await user.upload(screen.getByLabelText("Document photograph"), jpeg("doc.jpg"));
    expect(send.disabled).toBe(true); // no live photograph
    await user.upload(screen.getByLabelText("Live photograph"), jpeg("me.jpg"));
    expect(send.disabled).toBe(true); // no agreement
    await user.click(screen.getByRole("checkbox"));
    await waitFor(() => expect(send.disabled).toBe(false));
    await user.click(send);
    await waitFor(() => expect(fake.idv.documentImage).toHaveBeenCalledTimes(1));
    const [id, body] = fake.idv.documentImage.mock.calls[0] as [string, { image_base64: string; selfie_base64?: string; consent?: boolean; declared?: unknown; details?: { name?: string; number?: string; birth_date?: string } }];
    expect(id).toBe("c1");
    expect(body.image_base64.length).toBeGreaterThan(0);
    expect(body.selfie_base64?.length).toBeGreaterThan(0);
    expect(body.consent).toBe(true);
    expect(body.declared).toBeUndefined(); // no machine-readable-line details on this route
    expect(body.details).toBeUndefined(); // the face is the only thing compared
    expect(screen.queryByLabelText("Full name")).toBeNull();
  }, 60_000);

  it("explains a face that was not found or did not match as a retake, never a rejection", async () => {
    fake.idv.cases.mockResolvedValue({ items: [{ ...base, route: "WEB_FACE", signals: sent, reasons: ["FACE_DOES_NOT_MATCH_DOCUMENT_PHOTO"] }] });
    mount();
    expect(await screen.findByText("We could not verify this photograph")).toBeTruthy();
    expect(screen.getAllByText(/did not match the photograph on the document/).length).toBeGreaterThan(0); // in the banner and in the reasons
    expect(screen.queryByText("Rejected")).toBeNull();
  });

  it("does not put an old check on a retired route in front of the person: it shows the choice of document", async () => {
    fake.idv.cases.mockResolvedValue({
      items: [
        { ...base, case_id: "a", route: "WEB_AUTO", decision: "INDETERMINATE" },
        { ...base, case_id: "b", route: "WEB_REVIEW", decision: "REVIEW_REQUIRED" },
        { ...base, case_id: "c", route: "CHIP" },
      ],
    });
    mount();
    expect(await screen.findByText("Choose your document")).toBeTruthy();
    expect(screen.queryByText(/two lines of characters/)).toBeNull(); // the old passport-lines form
    expect(screen.queryByLabelText("Live photograph")).toBeNull();
  });

  it("still counts an approved check from an older route", async () => {
    nav.next = "/admin/onboarding";
    fake.idv.cases.mockResolvedValue({ items: [{ ...base, route: "WEB_AUTO", decision: "APPROVED", state: "CLOSED", signals: sent }] });
    mount();
    expect(await screen.findByText("Identity verified")).toBeTruthy();
  });

  it("tells the person to start a new check when a face-match check was stopped for review, and offers no upload into it", async () => {
    fake.idv.cases.mockResolvedValue({ items: [{ ...base, route: "WEB_FACE", decision: "REVIEW_REQUIRED", signals: sent, reasons: ["CAPTURE_INJECTION_RISK"] }] });
    mount();
    expect(await screen.findByText("Please start a new check")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Send for verification" })).toBeNull();
    expect(screen.getByRole("button", { name: /different document/ })).toBeTruthy();
  });

  it("counts the attempts: three, then the check has ended and no fourth upload is offered", async () => {
    fake.idv.cases.mockResolvedValue({ items: [{ ...base, route: "WEB_FACE", signals: sent, reasons: ["FACE_DOES_NOT_MATCH_DOCUMENT_PHOTO"], attempts: { CAPTURE: 1 } }] });
    const first = mount();
    expect(await screen.findByText("Attempt 2 of 3")).toBeTruthy();
    first.unmount();
    fake.idv.cases.mockResolvedValue({ items: [{ ...base, route: "WEB_FACE", signals: sent, reasons: ["FACE_DOES_NOT_MATCH_DOCUMENT_PHOTO"], attempts: { CAPTURE: 3 } }] });
    mount();
    expect(await screen.findByText("All attempts used")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Send for verification" })).toBeNull(); // no fourth attempt
  });
});
