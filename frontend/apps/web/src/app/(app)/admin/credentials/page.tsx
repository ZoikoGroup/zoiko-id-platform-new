import type { Metadata } from "next";

import { CredentialsAdminPage } from "@/features/admin/credentials/page";

export const metadata: Metadata = { title: "Credentials" };

export default function Page() {
  return <CredentialsAdminPage />;
}
