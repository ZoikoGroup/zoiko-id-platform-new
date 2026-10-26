import type { Metadata } from "next";

import { DevelopersPage } from "@/features/admin/developers/developers-page";

export const metadata: Metadata = { title: "Developers" };

export default function Page() {
  return <DevelopersPage />;
}
