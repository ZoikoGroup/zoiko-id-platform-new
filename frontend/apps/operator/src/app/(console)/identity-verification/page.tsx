import type { Metadata } from "next";

import { IdentityOpsPage } from "@/features/identity/identity-ops-page";

export const metadata: Metadata = { title: "Identity verification" };

export default function Page() {
  return <IdentityOpsPage />;
}
