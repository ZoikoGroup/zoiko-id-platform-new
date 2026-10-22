import type { Metadata } from "next";

import { PrivacyPage } from "@/features/admin/privacy/privacy-page";

export const metadata: Metadata = { title: "Privacy & data rights" };

export default function Page() {
  return <PrivacyPage />;
}
