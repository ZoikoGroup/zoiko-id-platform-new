"use client";

import { useMe } from "../authority/shared";
import { CompanyVerificationPage } from "./company-verification";

/** Supplies who is signed in, so the page can say "you cannot record your own mandate" before the platform has to refuse. */
export function CompanyVerificationRoute() {
  return <CompanyVerificationPage me={useMe()} />;
}
