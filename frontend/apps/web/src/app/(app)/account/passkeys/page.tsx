import type { Metadata } from "next";

import { PasskeysPage } from "@/features/account/passkeys";

export const metadata: Metadata = { title: "Passkeys" };

export default function Page() {
  return <PasskeysPage />;
}
