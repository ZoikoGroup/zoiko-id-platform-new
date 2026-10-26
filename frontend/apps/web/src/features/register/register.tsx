"use client";

import { useQuery } from "@tanstack/react-query";
import { Banner, Button, ButtonLink, Card, Checkbox, FieldGroup, Select, TextField } from "@zoiko/ui";
import { Building2, CheckCircle2, CircleAlert, Fingerprint, Loader2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { carryQuery, companyNumberValid, leiValid, normalizeCompanyNumber } from "@/features/onboarding/carry";

type SignupOptions = {
  notice_version: string;
  registers: { namespace: "GB-COH"; name: string; country: string }[];
  locations: { cell_id: string; region: string | null; country: string | null; legal_zone: string | null; this_cell: boolean; signup_url: string | null }[];
  development_verification: boolean;
};

type SignupResult = {
  workspace: string;
  display_name: string;
  tenant_status: string;
  enrollment_ticket: string;
  organization: "DEVELOPMENT_UNVERIFIED" | "VERIFICATION_REQUIRED";
};

type RegistryHit = { legal_name: string; active: boolean; status: string; jurisdiction: string };

const WS = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;

/** "example-co" from "Example Co. Ltd" - the user can still edit it. */
function suggestWorkspace(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/\b(ltd|limited|inc|llc|plc|gmbh)\b\.?/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}

const MESSAGES: Record<string, string> = {
  TENANT_SLUG_TAKEN: "That workspace name is already taken. Pick another one.",
  SIGNUP_DISABLED: "Company sign-up is not enabled on this platform.",
  RATE_LIMITED: "Too many attempts from this network. Try again in a few minutes.",
  VALIDATION_FAILED: "Check the details and try again.",
  NOTICE_VERSION_STALE: "The privacy notice changed. Read the current notice, accept it, and try again.",
  ORGANIZATION_IDENTIFIER_INVALID: "That company number is not valid.",
};

async function getJson<T>(url: string): Promise<{ status: number; body: T & { error?: { code?: string; message?: string } } }> {
  const res = await fetch(url, { cache: "no-store", credentials: "same-origin" });
  return { status: res.status, body: (await res.json().catch(() => ({}))) as T & { error?: { code?: string; message?: string } } };
}

/** The value, once it has stopped changing for `ms` (live checks without a request per keystroke). */
function useSettled<T>(value: T, ms = 450): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return settled;
}

function locationLabel(l: SignupOptions["locations"][number]): string {
  if (!l.region && !l.country) return `${l.cell_id} (this environment)`;
  return `${l.legal_zone ?? l.country ?? ""} · ${l.region ?? ""} (${l.cell_id})`;
}

/** Register a company: creates its tenant with you as its pending owner (OVERLAY-001 §4). What you
 * declare - the register identifier, the privacy-notice version you accept and the data location -
 * is stored with the new tenant; the company is then verified against its official register and you
 * are matched to one of its officers before you become its owner (ORG-001 §19). The passkey you
 * create next is your account. */
export function Register() {
  const options = useQuery({
    queryKey: ["signup", "options"],
    queryFn: async () => {
      const r = await getJson<SignupOptions>("/api/signup/options");
      if (r.status !== 200) throw new Error(r.body.error?.message ?? "Sign-up is unavailable.");
      return r.body;
    },
  });
  const [company, setCompany] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [touched, setTouched] = useState(false);
  const [name, setName] = useState("");
  const [number, setNumber] = useState("");
  const [lei, setLei] = useState("");
  const [location, setLocation] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const ws = touched ? workspace : suggestWorkspace(company);
  const wsValid = WS.test(ws);
  const numberOk = companyNumberValid(number);
  const leiOk = !lei.trim() || leiValid(lei);
  const here = options.data?.locations.find((l) => l.this_cell);
  const chosen = options.data?.locations.find((l) => l.cell_id === (location || here?.cell_id));
  const elsewhere = chosen && !chosen.this_cell;

  // Live checks (debounced): workspace name free, and what the register says about the number.
  const settledWs = useSettled(wsValid ? ws : "");
  const wsCheck = useQuery({
    queryKey: ["signup", "workspace", settledWs],
    enabled: !!settledWs,
    queryFn: async () => (await getJson<{ available: boolean }>(`/api/workspace?name=${encodeURIComponent(settledWs)}`)).body,
  });
  const settledNumber = useSettled(numberOk ? normalizeCompanyNumber(number) : "");
  const lookup = useQuery({
    queryKey: ["signup", "registry", settledNumber],
    enabled: !!settledNumber,
    retry: false,
    queryFn: () => getJson<RegistryHit>(`/api/signup/registry/GB-COH/${encodeURIComponent(settledNumber)}`),
  });
  const taken = wsCheck.data?.available === false && settledWs === ws;
  const notFound = lookup.data?.status === 404 && settledNumber === normalizeCompanyNumber(number);
  const inactive = lookup.data?.status === 200 && lookup.data.body.active === false;

  const valid =
    !!options.data &&
    company.trim().length > 0 &&
    wsValid &&
    !taken &&
    name.trim().length > 0 &&
    numberOk &&
    leiOk &&
    !notFound &&
    !inactive &&
    !elsewhere &&
    accepted;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!valid || !options.data) return;
    setPending(true);
    setFailure(null);
    try {
      const res = await fetch("/api/signup", {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          company_name: company.trim(),
          workspace: ws,
          registration: { namespace: "GB-COH", identifier: normalizeCompanyNumber(number), lei: lei.trim() ? lei.trim().toUpperCase() : null },
          notice_version: options.data.notice_version,
          home_cell_id: chosen?.cell_id ?? null,
        }),
      });
      const body = (await res.json().catch(() => ({}))) as Partial<SignupResult> & { error?: { code?: string; message?: string } };
      if (res.status !== 201 || !body.enrollment_ticket) {
        const code = body.error?.code ?? "";
        setFailure(MESSAGES[code] ?? body.error?.message ?? "Registration failed. Please try again.");
        setPending(false);
        if (code === "NOTICE_VERSION_STALE") {
          setAccepted(false);
          void options.refetch();
        }
        return;
      }
      // The ticket is single use; the name only labels the passkey on this account.
      const q = new URLSearchParams({
        ticket: body.enrollment_ticket,
        label: name.trim().slice(0, 60),
        company: body.display_name ?? "",
        next: "/overview",
      });
      // The registration is stored with the tenant; the link carries it only as a fallback prefill.
      const carry = carryQuery({ companyNumber: number, lei, development: body.organization === "DEVELOPMENT_UNVERIFIED" });
      window.location.assign(`/enroll?${q.toString()}${carry ? `&${carry}` : ""}`);
    } catch {
      setFailure("The service is temporarily unavailable. Please try again.");
      setPending(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Register your company</h1>
        <p className="mt-1 text-sm text-ink-muted">
          Create your company&apos;s workspace. Once the company is verified, you become its first administrator.
        </p>
      </div>

      {options.isError ? (
        <Banner tone="danger" title="Sign-up is unavailable">
          {options.error instanceof Error ? options.error.message : "Try again later."}
        </Banner>
      ) : null}
      {failure ? (
        <Banner tone="danger" live="assertive" title="Couldn't register">
          {failure}
        </Banner>
      ) : null}

      <Card>
        <form className="space-y-4" onSubmit={(e) => void submit(e)}>
          <TextField label="Company name" isRequired value={company} onChange={setCompany} description="As people in your company know it." />
          <div>
            <TextField
              label="Workspace name"
              isRequired
              mono
              value={ws}
              onChange={(v) => {
                setTouched(true);
                setWorkspace(v.toLowerCase());
              }}
              description="Your team types this to sign in. Lower-case letters, digits and hyphens."
              errorMessage={ws && !wsValid ? "Use lower-case letters, digits and inner hyphens." : taken ? "That workspace name is taken." : undefined}
            />
            {wsValid && settledWs === ws && wsCheck.data?.available ? (
              <p className="mt-1 flex items-center gap-1 text-xs text-ok-fg">
                <CheckCircle2 aria-hidden className="size-3.5" /> Available
              </p>
            ) : null}
          </div>

          <FieldGroup legend="Company registration" description="Used to verify the company against its official register. Public register data only.">
            <Select
              label="Register"
              value="GB-COH"
              onChange={() => undefined}
              options={(options.data?.registers ?? [{ namespace: "GB-COH", name: "Companies House", country: "GB" }]).map((r) => ({
                id: r.namespace,
                label: `${r.country === "GB" ? "United Kingdom" : r.country} — ${r.name}`,
              }))}
              isRequired
            />
            <div>
              <TextField
                label="Company number"
                isRequired
                mono
                value={number}
                onChange={(v) => setNumber(v.toUpperCase())}
                placeholder="01234567"
                description="The 8-character Companies House number, e.g. 01234567 or SC123456."
                errorMessage={
                  number && !numberOk
                    ? "Enter 8 digits, or two letters and 6 digits."
                    : notFound
                      ? "Companies House has no company with this number."
                      : inactive
                        ? "Companies House shows this company as not active."
                        : undefined
                }
              />
              {numberOk && lookup.isFetching ? (
                <p className="mt-1 flex items-center gap-1 text-xs text-ink-muted">
                  <Loader2 aria-hidden className="size-3.5 animate-spin" /> Checking Companies House…
                </p>
              ) : lookup.data?.status === 200 && lookup.data.body.active ? (
                <p className="mt-1 flex items-center gap-1 text-xs text-ok-fg">
                  <CheckCircle2 aria-hidden className="size-3.5" /> {lookup.data.body.legal_name}
                </p>
              ) : lookup.data && lookup.data.status >= 500 ? (
                <p className="mt-1 flex items-center gap-1 text-xs text-ink-muted">
                  <CircleAlert aria-hidden className="size-3.5" /> Companies House can&apos;t be checked right now; it is checked again at verification.
                </p>
              ) : null}
            </div>
            <TextField
              label="LEI (optional)"
              mono
              value={lei}
              onChange={(v) => setLei(v.toUpperCase())}
              description="Your Legal Entity Identifier, if the company has one. It corroborates the register record."
              errorMessage={lei && !leiOk ? "An LEI is 20 characters: 18 letters or digits and 2 check digits." : undefined}
            />
          </FieldGroup>

          <Select
            label="Where your company's data is stored"
            value={chosen?.cell_id ?? ""}
            onChange={setLocation}
            options={(options.data?.locations ?? []).map((l) => ({ id: l.cell_id, label: locationLabel(l) }))}
            description="Chosen once: moving a company later is a governed migration."
            isRequired
          />
          {elsewhere ? (
            <Banner tone="info" title="Register in that region">
              Companies stored in {chosen?.legal_zone ?? chosen?.cell_id} register on that region&apos;s service
              {chosen?.signup_url ? (
                <>
                  :{" "}
                  <a className="font-medium text-brand-700 hover:underline" href={`${chosen.signup_url.replace(/\/$/, "")}/register`}>
                    continue there
                  </a>
                </>
              ) : null}
              .
            </Banner>
          ) : null}

          <TextField label="Your name" isRequired value={name} onChange={setName} description="Only used to label your passkey, e.g. “Jane's laptop”." />

          <Checkbox isSelected={accepted} onChange={setAccepted}>
            I have read the{" "}
            <a href="/legal/privacy" target="_blank" rel="noopener" className="font-medium text-brand-700 hover:underline">
              privacy notice
            </a>
            {options.data ? ` (version ${options.data.notice_version})` : ""} and register this company on its behalf.
          </Checkbox>

          <Button type="submit" size="lg" className="w-full" pending={pending} isDisabled={!valid} icon={<Building2 aria-hidden className="size-5" />}>
            Create workspace
          </Button>
        </form>
      </Card>

      {options.data?.development_verification ? (
        <Banner tone="warn" icon={CircleAlert} title="Development environment">
          Companies are verified with a synthetic development record here; production checks the register and matches you to an officer.
        </Banner>
      ) : null}

      <div className="rounded-card border border-border bg-surface p-4 text-sm text-ink-muted">
        <div className="mb-1 flex items-center gap-2 font-medium text-ink">
          <Fingerprint aria-hidden className="size-4" /> What happens next
        </div>
        <ol className="list-decimal space-y-1 pl-5">
          <li>You create a passkey on this device. It is your sign-in; there is no password.</li>
          <li>We check the company against Companies House and confirm you are one of its officers.</li>
          <li>You become the company&apos;s owner and set it up step by step: domains, identity, applications, then launch.</li>
          <li>From Team &amp; invitations you invite colleagues; each creates their own passkey.</li>
        </ol>
      </div>

      <div className="flex justify-center">
        <ButtonLink href="/sign-in" variant="ghost" size="sm">
          Already registered? Sign in
        </ButtonLink>
      </div>
    </div>
  );
}
