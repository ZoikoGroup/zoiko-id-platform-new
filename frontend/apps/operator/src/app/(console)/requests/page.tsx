import type { Metadata } from "next";

import { MyAccessPage } from "@/features/requests";

export const metadata: Metadata = { title: "My access" };

export default function Page() {
  return <MyAccessPage />;
}
