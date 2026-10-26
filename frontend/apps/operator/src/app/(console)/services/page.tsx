import type { Metadata } from "next";

import { ServicesPage } from "@/features/services";

export const metadata: Metadata = { title: "Services" };

export default function Page() {
  return <ServicesPage />;
}
