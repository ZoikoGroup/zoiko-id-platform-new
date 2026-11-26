import type { Metadata } from "next";

import { AgentControlsPage } from "@/features/admin/agents/controls/controls-page";

export const metadata: Metadata = { title: "Agent controls" };

export default function Page() {
  return <AgentControlsPage />;
}
