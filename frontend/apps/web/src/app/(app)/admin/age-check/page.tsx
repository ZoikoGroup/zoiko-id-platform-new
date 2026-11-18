import type { Metadata } from "next";

import { AgeCheckPage } from "@/features/admin/age-check/age-check";

export const metadata: Metadata = { title: "Age check" };

export default function Page() {
  return <AgeCheckPage />;
}
