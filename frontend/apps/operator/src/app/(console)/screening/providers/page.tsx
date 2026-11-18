import type { Metadata } from "next";

import { ScreeningProvidersPage } from "@/features/screening/providers";

export const metadata: Metadata = { title: "Screening providers" };

export default function Page() {
  return <ScreeningProvidersPage />;
}
