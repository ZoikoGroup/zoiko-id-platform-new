import type { Metadata } from "next";

import { OperatorSignIn } from "@/features/sign-in";

export const metadata: Metadata = { title: "Sign in" };

export default async function SignInPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  return <OperatorSignIn returnTo={sp.returnTo} reason={sp.reason} passwordSignIn={process.env.ZID_OPERATOR_PASSWORD_SIGNIN === "true"} />;
}
