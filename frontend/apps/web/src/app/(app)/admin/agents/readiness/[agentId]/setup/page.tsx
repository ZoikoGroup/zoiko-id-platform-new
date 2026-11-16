import type { Metadata } from "next";

import { SetupWizardPage } from "@/features/admin/agents/readiness/setup-wizard";

export const metadata: Metadata = { title: "Set up access readiness" };

export default async function Page({ params }: { params: Promise<{ agentId: string }> }) {
  return <SetupWizardPage agentId={(await params).agentId} />;
}
