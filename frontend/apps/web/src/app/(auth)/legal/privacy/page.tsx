import type { Metadata } from "next";

export const metadata: Metadata = { title: "Privacy notice" };

/** The privacy notice a registrant acknowledges at company sign-up (PRIV-001 notice at collection).
 * Its version must equal the cell's ZID_TENANT_SIGNUP_NOTICE_VERSION; the cell refuses a sign-up
 * that acknowledged any other version. The wording describes exactly what sign-up stores and is to
 * be approved by Privacy/Legal before production (bump both versions on any change). */
const NOTICE_VERSION = "2026-10-22";

export default function PrivacyNoticePage() {
  return (
    <article className="space-y-5 text-sm leading-6 text-ink">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Privacy notice: company registration</h1>
        <p className="text-ink-muted">Version {NOTICE_VERSION}</p>
      </header>
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
          You can see, export or delete your personal data from <span className="font-medium">My account → Privacy</span> once signed in. Company records
          kept as evidence follow their retention schedule.
        </p>
      </section>
      <p>
        <a href="/register" className="font-medium text-brand-700 hover:underline">
          Back to registration
        </a>
      </p>
    </article>
  );
}
