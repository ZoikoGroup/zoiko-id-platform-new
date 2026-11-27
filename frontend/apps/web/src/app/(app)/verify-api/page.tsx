import type { Metadata } from "next";

import { IdentityVerificationPage } from "@/features/admin/developers/identity-verification-page";

export const metadata: Metadata = { title: "Identity verification" };

export default function Page() {
  return <IdentityVerificationPage />;
}
