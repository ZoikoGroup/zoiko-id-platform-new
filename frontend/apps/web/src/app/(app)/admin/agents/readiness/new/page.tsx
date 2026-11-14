import type { Metadata } from "next";

import { SetupWizardPage } from "@/features/admin/agents/readiness/setup-wizard";

export const metadata: Metadata = { title: "Create access readiness agent" };

export default function Page() {
  return <SetupWizardPage />;
}
