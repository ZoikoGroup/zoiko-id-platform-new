import type { Metadata } from "next";

import { OperatorSignIn } from "@/features/sign-in";

// The tab reads "Operator sign-in · Operator Console", so it is never mistaken for the company site's "Sign in · Zoiko iD".
export const metadata: Metadata = { title: "Operator sign-in" };

export default async function SignInPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  // ZID_COMPANY_SIGN_IN_URL: where a customer who opened this page by mistake should go (the company site). Unset, no link is shown.
  return <OperatorSignIn returnTo={sp.returnTo} reason={sp.reason} companyUrl={process.env.ZID_COMPANY_SIGN_IN_URL} />;
}
