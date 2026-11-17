import type { Metadata } from "next";

import { CompanyVerificationRoute } from "@/features/admin/company-verification/route";

export const metadata: Metadata = { title: "Company verification" };

export default function Page() {
  return <CompanyVerificationRoute />;
}
