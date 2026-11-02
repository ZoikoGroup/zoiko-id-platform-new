import type { Metadata } from "next";

import { SignUpStatus } from "@/features/sign-up/status";

export const metadata: Metadata = { title: "Your Zoiko iD", robots: { index: false, follow: false } };

export default function SignUpStatusPage() {
  return (
    <div className="space-y-5">
      <div className="text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Your Zoiko iD</h1>
        <p className="mt-1 text-sm text-ink-muted">Where your sign-up stands and what to do next.</p>
      </div>
      <SignUpStatus />
    </div>
  );
}
