import type { Metadata } from "next";

import { IdentityProvidersPage } from "@/features/admin/identity-providers/page";

export const metadata: Metadata = { title: "Identity providers" };

export default function Page() {
  return <IdentityProvidersPage />;
}
