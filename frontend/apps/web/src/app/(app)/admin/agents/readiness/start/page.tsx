import type { Metadata } from "next";

import { GuidedSetupPage } from "@/features/admin/agents/readiness/guided-setup";

export const metadata: Metadata = { title: "Set up the access readiness agent" };

export default function Page() {
  return <GuidedSetupPage />;
}
