import type { Metadata } from "next";

import { HostedFlow } from "@/features/verify-session/hosted-flow";

export const metadata: Metadata = { title: "Verify your identity", robots: { index: false, follow: false } };
export const dynamic = "force-dynamic";

/** A person a business sent here (ZID-CP-WF01). No sign-in: the one-use link in the address fragment opens their session. */
export default function Page() {
  return <HostedFlow />;
}
