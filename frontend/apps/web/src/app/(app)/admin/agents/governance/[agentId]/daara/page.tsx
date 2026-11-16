import type { Metadata } from "next";

import { DaaraSetupPage } from "@/features/admin/agents/governance/daara-setup";

export const metadata: Metadata = { title: "Access readiness agent" };

export default async function Page({ params }: { params: Promise<{ agentId: string }> }) {
  return <DaaraSetupPage agentId={(await params).agentId} />;
}
