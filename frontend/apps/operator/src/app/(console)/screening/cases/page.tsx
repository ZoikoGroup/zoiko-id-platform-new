import type { Metadata } from "next";

import { ScreeningCasesPage } from "@/features/screening/cases";

export const metadata: Metadata = { title: "Screening cases" };

export default function Page() {
  return <ScreeningCasesPage />;
}
