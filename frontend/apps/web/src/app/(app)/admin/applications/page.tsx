import type { Metadata } from "next";

import { ApplicationsPage } from "@/features/onboarding/applications-page";

export const metadata: Metadata = { title: "Applications" };

export default function Page() {
  return <ApplicationsPage />;
}
