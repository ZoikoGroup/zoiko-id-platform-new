"use client";

import { useQuery } from "@tanstack/react-query";
import type { RegistrationCapabilities } from "@zoiko/api-client";
import { Banner, Button, ButtonLink, Card, RadioCards, TextField } from "@zoiko/ui";
import { ShieldCheck } from "lucide-react";
import { useMemo, useRef, useState, type FormEvent } from "react";

import { SignUpStatus } from "./status";
import {
  canSubmit,
  countryState,
  filterCountries,
  GENERIC_MESSAGE,
  JOURNEYS,
  serverMessage,
  validate,
  type FieldError,
  type Journey,
} from "./logic";

async function loadCapabilities(country: string): Promise<RegistrationCapabilities> {
  const q = country ? `?country=${encodeURIComponent(country)}` : "";
  const res = await fetch(`/api/registration/capabilities${q}`, { cache: "no-store", credentials: "same-origin" });
  const body = (await res.json().catch(() => ({}))) as RegistrationCapabilities & { error?: { code?: string } };
  if (!res.ok) throw new Error(serverMessage(body.error?.code));
  return body;
}

/** Federated sign-in options appear only when the server lists a provider approved and enabled for sign-up. Never otherwise. */
export function SsoOptions({ providers }: { providers: string[] }) {
  if (providers.length === 0) return null;
  return (
    <div className="space-y-2" aria-label="Single sign-on">
      {providers.map((p) => (
        <ButtonLink key={p} href={`/sign-in?provider=${encodeURIComponent(p)}`} variant="secondary" size="lg" className="w-full min-h-11">
          Continue with {p.charAt(0).toUpperCase() + p.slice(1)}
        </ButtonLink>
      ))}
    </div>
  );
}

/** WF-13A-01: country, journey and email, then one generic answer. The server derives everything else (region, tenant, role, status);
 * nothing on this page is an authorization decision. The default is unselected for the journey and the country. */
export function SignUp() {
  const [journey, setJourney] = useState<Journey | null>(null);
  const [country, setCountry] = useState("");
  const [search, setSearch] = useState("");
  const [email, setEmail] = useState("");
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [failure, setFailure] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [requestId, setRequestId] = useState<string | null>(null);
  const idempotency = useRef(crypto.randomUUID());
  const summary = useRef<HTMLDivElement>(null);

  const caps = useQuery({ queryKey: ["registration", "capabilities", country], queryFn: () => loadCapabilities(country), retry: false });
  const state = countryState(caps.data, country);
  const locale = typeof navigator === "undefined" ? "en" : navigator.language || "en";
  const countries = useMemo(() => filterCountries(search, locale), [search, locale]);
  const errorFor = (f: FieldError["field"]) => errors.find((e) => e.field === f)?.message;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (pending) return; // one submission at a time
    const found = validate({ journey, country, email });
    setErrors(found);
    setFailure(null);
    if (found.length > 0 || !caps.data || !canSubmit(state)) {
      requestAnimationFrame(() => summary.current?.focus());
      return;
    }
    setPending(true);
    try {
      const res = await fetch("/api/registration/intents", {
        method: "POST",
        headers: { "content-type": "application/json", "idempotency-key": idempotency.current },
        credentials: "same-origin",
        body: JSON.stringify({
          country,
          journey,
          email: email.trim(),
          locale: locale.slice(0, 35),
          notice_version: caps.data.notice_version,
          nonce: caps.data.nonce,
        }),
      });
      const body = (await res.json().catch(() => ({}))) as { request_id?: string; error?: { code?: string } };
      if (res.status !== 202 || !body.request_id) {
        setFailure(serverMessage(body.error?.code));
        if (body.error?.code === "NOTICE_VERSION_STALE") void caps.refetch();
        idempotency.current = crypto.randomUUID(); // a corrected resubmission is a new request; a refresh is not
        setPending(false);
        requestAnimationFrame(() => summary.current?.focus());
        return;
      }
      setRequestId(body.request_id); // the server also holds it in an HttpOnly cookie for a resumed tab
    } catch {
      setFailure("The service is temporarily unavailable. Please try again.");
      setPending(false);
      requestAnimationFrame(() => summary.current?.focus());
    }
  }

  if (requestId) {
    return (
      <div className="space-y-5">
        <div className="text-center">
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Check your email</h1>
          <p className="mt-1 text-sm text-ink-muted" role="status">
            {GENERIC_MESSAGE}
          </p>
        </div>
        <SignUpStatus />
        <div className="flex justify-center">
          <Button variant="ghost" size="sm" className="min-h-11" onPress={() => { setRequestId(null); setPending(false); idempotency.current = crypto.randomUUID(); }}>
            Use a different email address
          </Button>
        </div>
      </div>
    );
  }

  const messages = [...errors.map((x) => x.message), ...(failure ? [failure] : [])];
  return (
    <div className="space-y-5">
      <div className="text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Create your Zoiko iD.</h1>
        <p className="mt-1 text-sm text-ink-muted">Your secure starting point for digital trust.</p>
      </div>

      {messages.length > 0 ? (
        <div ref={summary} tabIndex={-1} role="alert" className="outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
          <Banner tone="danger" title="Check the details and try again">
            <ul className="list-disc pl-5">
              {messages.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          </Banner>
        </div>
      ) : null}
      {caps.isError ? (
        <Banner tone="warn" title="Sign-up isn't available right now">
          {caps.error instanceof Error ? caps.error.message : "Try again later."}
        </Banner>
      ) : null}

      <Card>
        <form className="space-y-5" onSubmit={(e) => void submit(e)} noValidate>
          <RadioCards
            label="What are you creating this for?"
            options={JOURNEYS.map((j) => ({ id: j.id, label: j.label, description: j.description }))}
            value={journey}
            onChange={(v) => setJourney(v as Journey)}
            description="This only chooses what happens next. It does not make you an administrator or verify anything."
          />
          {errorFor("journey") ? <p className="text-xs font-medium text-danger-fg">{errorFor("journey")}</p> : null}

          <div className="space-y-2">
            <TextField inputClassName="min-h-11" label="Search countries" type="search" value={search} onChange={setSearch} autoComplete="off" description="Type a country name or its two-letter code." />
            <div className="flex flex-col gap-1.5">
              <label htmlFor="country" className="text-sm font-medium text-ink">
                Country or region where you live<span aria-hidden className="text-danger-fg"> *</span>
              </label>
              <select
                id="country"
                name="country"
                required
                value={country}
                onChange={(e) => setCountry(e.target.value)}
                aria-invalid={Boolean(errorFor("country"))}
                aria-describedby="country-help"
                className="min-h-11 w-full rounded-control border border-border-strong bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/25"
              >
                <option value="">Choose a country</option>
                {countries.map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.name} ({c.code})
                  </option>
                ))}
              </select>
              <p id="country-help" className="text-xs text-ink-subtle">
                You choose this. It is not guessed from your network or language, and it sets where your account is kept.
              </p>
              {errorFor("country") ? <p className="text-xs font-medium text-danger-fg">{errorFor("country")}</p> : null}
            </div>
            {state.kind === "unavailable" ? (
              <Banner tone="warn" live="polite" title="Not available yet">
                {state.message}
                {state.contact ? (
                  <>
                    {" "}
                    You can contact{" "}
                    <a className="inline-flex min-h-11 items-center font-medium underline" href={`mailto:${state.contact}`}>
                      {state.contact}
                    </a>
                    .
                  </>
                ) : null}
              </Banner>
            ) : null}
            {state.kind === "other_region" ? (
              <Banner tone="info" live="polite" title="Available in another region">
                Accounts for this country are held in a different region.
                {state.url ? (
                  <>
                    {" "}
                    <a className="font-medium text-brand-700 hover:underline" href={`${state.url.replace(/\/$/, "")}/sign-up`}>
                      Continue there
                    </a>
                    .
                  </>
                ) : null}
              </Banner>
            ) : null}
            {state.kind === "ok" && journey === "ORGANIZATION" && !state.organizationClaims ? (
              <Banner tone="info" live="polite" title="Organization checks aren't open here yet">
                You can create your account now. Starting an organization claim isn&apos;t available for this country yet.
              </Banner>
            ) : null}
          </div>

          <TextField
            inputClassName="min-h-11"
            label="Email address"
            type="email"
            isRequired
            autoComplete="email"
            inputMode="email"
            value={email}
            onChange={setEmail}
            errorMessage={errorFor("email")}
            description="We'll send one link to confirm this inbox is yours."
          />

          <Button type="submit" size="lg" className="w-full min-h-11" pending={pending} isDisabled={!caps.data || state.kind === "unavailable" || state.kind === "other_region"} icon={<ShieldCheck aria-hidden className="size-5" />}>
            {pending ? "Continuing…" : "Continue securely"}
          </Button>
          <p className="text-xs text-ink-subtle">
            We use your information to establish and protect your account. Additional identity or company checks are requested only when needed.
          </p>
        </form>
      </Card>

      <SsoOptions providers={caps.data?.identity_providers ?? []} />

      <nav aria-label="Legal and sign-in" className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1 text-sm">
        <a className="inline-flex min-h-11 items-center font-medium text-brand-700 hover:underline" href="/sign-in">
          Already have a Zoiko iD? Sign in
        </a>
        <a className="inline-flex min-h-11 items-center text-ink-muted hover:underline" href="/legal/terms">
          Terms of Service
        </a>
        <a className="inline-flex min-h-11 items-center text-ink-muted hover:underline" href="/legal/privacy">
          Privacy Notice
        </a>
        <a className="inline-flex min-h-11 items-center text-ink-muted hover:underline" href="/help">
          Help
        </a>
      </nav>
    </div>
  );
}
