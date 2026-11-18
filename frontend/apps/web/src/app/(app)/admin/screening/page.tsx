import type { Metadata } from "next";

import { ScreeningPage } from "@/features/admin/screening/screening";

export const metadata: Metadata = { title: "Screening" };

export default function Page() {
  return <ScreeningPage />;
}
