import Link from "next/link";

/** The same generic answer as the app's not-found page, without its own `<main>`: inside the sign-in layout the layout already provides the
 * page's one main landmark (a second one is an accessibility failure), and the layout supplies the mark. */
export default function AuthNotFound() {
  return (
    <section className="flex flex-col items-center gap-4 text-center" aria-labelledby="nf-h">
      <h1 id="nf-h" className="text-xl font-semibold text-ink">
        This page isn&apos;t available
      </h1>
      <p className="max-w-sm text-sm text-ink-muted">It may not exist, or your access may not include it.</p>
      <Link href="/account" className="text-sm font-medium text-brand-600 hover:underline">
        Go to my account
      </Link>
    </section>
  );
}
