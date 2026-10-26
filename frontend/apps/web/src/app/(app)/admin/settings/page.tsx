import type { Metadata } from "next";

import { SettingsPage } from "@/features/onboarding/settings-page";

export const metadata: Metadata = { title: "Company settings" };

export default function Page() {
  return <SettingsPage />;
}
