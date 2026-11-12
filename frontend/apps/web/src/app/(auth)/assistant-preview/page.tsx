import { AssistantWidget } from "@zoiko/assistant-widget";
import { ZoikoMark } from "@zoiko/ui";
import type { Metadata } from "next";

export const metadata: Metadata = { title: "Zoiko iD Assistant preview", robots: { index: false, follow: false } };

/** Engineering preview of the public assistant (ZI-CHAT-UX-001). Public and unauthenticated on purpose: the assistant is anonymous,
 * information-only and holds no Zoiko iD session. It is mounted here so the widget can be reviewed before it is embedded in the website. */
export default function AssistantPreviewPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold tracking-tight text-ink">Zoiko iD Assistant (preview)</h1>
      <p className="text-sm text-ink-muted">
        This is a working preview of the public assistant. It explains Zoiko iD concepts and points to public pages. It can&apos;t see or change any
        account, and you should not type passwords, one-time codes or identity documents here.
      </p>
      <p className="text-sm text-ink-muted">
        Answers marked <strong>sample content</strong> come from engineering samples that have not been approved for publication. Open the assistant with the button in the
        bottom-right corner.
      </p>
      <AssistantWidget apiBase="/api/public/chat/v1" logo={<ZoikoMark />} fallbackLinks={[{ label: "Back to sign in", href: "/sign-in" }]} />
    </div>
  );
}
