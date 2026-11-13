import type { Metadata } from "next";

import { SuppliersPage } from "@/features/regulatory/suppliers";

export const metadata: Metadata = { title: "Suppliers" };

export default function Page() {
  return <SuppliersPage />;
}
