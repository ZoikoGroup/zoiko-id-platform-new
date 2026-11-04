import type { Metadata } from "next";

import { PRIVACY_NOTICE_VERSION } from "@/lib/legal";

export const metadata: Metadata = { title: "Privacy notice" };

/** The privacy notice a person acknowledges at sign-up (PRIV-001 notice at collection), for a personal account and for a company.
 * Its version (`lib/legal.ts`) must equal the cell's ZID_TENANT_SIGNUP_NOTICE_VERSION; the cell refuses a sign-up that acknowledged any
 * other version. The wording describes exactly what sign-up stores and is to be approved by Privacy and Legal before production (bump
 * both versions on any change). */
const NOTICE_VERSION = PRIVACY_NOTICE_VERSION;

export default function PrivacyNoticePage() {
  return (
    <article className="space-y-5 text-sm leading-6 text-ink">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Privacy notice: sign-up</h1>
        <p className="text-ink-muted">Version {NOTICE_VERSION}</p>
      </header>
      <section className="space-y-2">
        <h2 className="text-base font-semibold">What we store when you create an account</h2>
        <ul className="list-disc space-y-1 pl-5">
          <li>The country you choose. It decides which region your account is created in, and nothing is stored until a region is found for it.</li>
          <li>
            The email address you type. It is kept in readable form only until you confirm it or the request ends. After that we keep only a one-way
            fingerprint of it, so we can tell that the address already has an account.
          </li>
          <li>Whether you are signing up for yourself or for an organization, your language setting, and which version of this notice you saw.</li>
          <li>The single-use link we email you, kept only as a one-way fingerprint, and how many times it was tried.</li>
          <li>Your passkey&apos;s public key and the label you give it. There is no password, and we never receive your fingerprint or face.</li>
        </ul>
        <p>
          We do not ask for identity documents, a photo, a phone number, proof of address or a card to create an account. A new account is active but
          not identity verified. Choosing &ldquo;for an organization&rdquo; starts a claim that is checked separately; it does not make you an owner.
        </p>
        <p>
          We send one email, to the address you typed, with a link that works once. Requests that end are deleted after 30 days. The fingerprint that
          links your address to your account is kept while the account exists and is removed if your account is erased, after which the address can be
          used again.
        </p>
      </section>
      <section className="space-y-2">
        <h2 className="text-base font-semibold">What we store when you register a company</h2>
        <ul className="list-disc space-y-1 pl-5">
          <li>The company name and workspace name you choose.</li>
          <li>The company registration number (and LEI, if you give one) and the register it belongs to.</li>
          <li>The data location (cell) you choose. Your company&apos;s data is stored there.</li>
          <li>Which version of this notice you acknowledged, and when.</li>
          <li>Your passkey&apos;s public key and the label you give it. There is no password, and we never receive your fingerprint or face.</li>
        </ul>
      </section>
      <section className="space-y-2">
        <h2 className="text-base font-semibold">Why</h2>
        <p>
          To create your company&apos;s workspace, to verify the company against its official register, and to confirm that you are one of its officers
          before you become its owner. Register data is public; we re-read it from the register rather than trusting what was typed.
        </p>
      </section>
      <section className="space-y-2">
        <h2 className="text-base font-semibold">Identity verification</h2>
        <p>
          To match you to a company officer, you complete identity verification. Only a commitment to your verified name is compared with the register;
          your documents are not shared with your company.
        </p>
      </section>
      <section className="space-y-2">
        <h2 className="text-base font-semibold">Your rights</h2>
        <p>
          You can see, export or delete your personal data from <span className="font-medium">My account → Privacy</span> once signed in; deleting your
          account removes the sign-up records above. Company records kept as evidence follow their retention schedule.
        </p>
      </section>
      <p>
        <a href="/sign-up" className="font-medium text-brand-700 hover:underline">
          Back to sign-up
        </a>
      </p>
    </article>
  );
}
