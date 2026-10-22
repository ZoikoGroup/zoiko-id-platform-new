import type { Metadata } from "next";

import { Recover } from "@/features/recover/recover";

export const metadata: Metadata = { title: "Recover your account" };

export default function RecoverPage() {
  return <Recover />;
}
