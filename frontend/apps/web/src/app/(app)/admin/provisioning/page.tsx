import type { Metadata } from "next";

import { ProvisioningPage } from "@/features/admin/provisioning/provisioning-page";

export const metadata: Metadata = { title: "Provisioning" };

export default function Page() {
  return <ProvisioningPage />;
}
