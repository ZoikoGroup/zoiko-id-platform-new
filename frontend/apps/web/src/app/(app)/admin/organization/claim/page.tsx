import type { Metadata } from "next";

import { FindOrganizationPage } from "@/features/onboarding/org-claim-page";

export const metadata: Metadata = { title: "Find your organization" };

export default function Page() {
  return <FindOrganizationPage />;
}
