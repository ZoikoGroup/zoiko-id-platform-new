import type { Metadata } from "next";

import { AgentsPage } from "@/features/admin/agents/agents-page";

export const metadata: Metadata = { title: "Agents & tools" };

export default function Page() {
  return <AgentsPage />;
}
