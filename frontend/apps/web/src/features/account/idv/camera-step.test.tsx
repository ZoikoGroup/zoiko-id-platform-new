import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CameraStep } from "./camera-step";

const photo = (name = "doc.jpg", kb = 30, type = "image/jpeg") => new File([new Uint8Array(kb * 1024)], name, { type });

type Props = Partial<React.ComponentProps<typeof CameraStep>>;
function Step(p: Props) {
  return (
    <CameraStep
      title="Photograph of your passport"
      hint="Photograph the page with your photo."
      facing="environment"
      frame="document"
      allowUpload
      uploadLabel="Document photograph"
      deviceLabel="Document photograph from your device camera"
      onBack={() => undefined}
      onUse={() => undefined}
      {...p}
    />
  );
}

beforeEach(() => {
  // jsdom has no camera and no preview addresses: leave the camera out, give the previews two
  Object.defineProperty(navigator, "mediaDevices", { value: undefined, configurable: true });
  URL.createObjectURL = vi.fn(() => "blob:preview");
  URL.revokeObjectURL = vi.fn();
});
afterEach(cleanup);

describe("the capture screen without a camera", () => {
  it("says so in words and offers the device's camera, a picture upload for the document, and another try", async () => {
    render(<Step />);
    expect(await screen.findByText(/could not open your camera/)).toBeTruthy();
    expect(screen.getByLabelText("Document photograph")).toBeTruthy();
    expect(screen.getByLabelText("Document photograph from your device camera")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try camera again" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "Take photo" }) as HTMLButtonElement).disabled).toBe(true); // nothing to take a photograph of
  });

  it("offers no picture upload for a selfie: it is taken live, or with the device's camera app", async () => {
    render(<Step frame="face" facing="user" allowUpload={false} deviceLabel="Live photograph" />);
    expect(await screen.findByText(/could not open your camera/)).toBeTruthy();
    expect(screen.queryByLabelText("Document photograph")).toBeNull();
    expect(screen.getByLabelText("Live photograph")).toBeTruthy();
  });

  it("asks the device's camera app for the right camera: the back one for a document, the front one for a face", async () => {
    const { unmount } = render(<Step />);
    expect(screen.getByLabelText("Document photograph from your device camera").getAttribute("capture")).toBe("environment");
    unmount();
    render(<Step frame="face" facing="user" allowUpload={false} deviceLabel="Live photograph" />);
    expect(screen.getByLabelText("Live photograph").getAttribute("capture")).toBe("user");
  });
});

describe("a photograph, once taken or chosen", () => {
  it("shows the photograph itself and lets the person retake it or use it", async () => {
    const onUse = vi.fn();
    const user = userEvent.setup();
    render(<Step onUse={onUse} />);
    const f = photo("front.jpg");
    await user.upload(screen.getByLabelText("Document photograph"), f);
    expect((screen.getByAltText("Your document photograph") as HTMLImageElement).src).toContain("blob:preview");
    expect(screen.getByRole("button", { name: "Retake" })).toBeTruthy();
    expect(onUse).not.toHaveBeenCalled(); // nothing is accepted until the person says so
    await user.click(screen.getByRole("button", { name: "Use this photo" }));
    expect(onUse).toHaveBeenCalledWith(f);
  });

  it("goes back to the camera on Retake and keeps nothing of the first picture", async () => {
    const user = userEvent.setup();
    render(<Step />);
    await user.upload(screen.getByLabelText("Document photograph"), photo());
    await user.click(screen.getByRole("button", { name: "Retake" }));
    expect(screen.queryByAltText("Your document photograph")).toBeNull();
    expect(screen.queryByRole("button", { name: "Use this photo" })).toBeNull();
    expect(await screen.findByText(/could not open your camera/)).toBeTruthy();
  });

  it("refuses a file the platform cannot read, in words, and does not offer it", async () => {
    const user = userEvent.setup();
    render(<Step />);
    for (const [bad, why] of [
      [photo("big.jpg", 7 * 1024), /too large/],
      [photo("tiny.jpg", 2), /too small/],
    ] as const) {
      await user.upload(screen.getByLabelText("Document photograph"), bad);
      expect((await screen.findByRole("alert")).textContent).toMatch(why);
      expect(screen.queryByRole("button", { name: "Use this photo" })).toBeNull();
    }
    await user.upload(screen.getByLabelText("Document photograph"), photo("ok.jpg"));
    expect(screen.queryByRole("alert")).toBeNull(); // a good one clears the message
    expect(screen.getByRole("button", { name: "Use this photo" })).toBeTruthy();
  });

  it("goes back without keeping anything", async () => {
    const onBack = vi.fn();
    render(<Step onBack={onBack} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Back" }));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it("releases the preview when the screen goes away", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<Step />);
    await user.upload(screen.getByLabelText("Document photograph"), photo());
    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview");
  });
});
