"use client";

import { Banner, Button, ButtonLink, Card, TextField } from "@zoiko/ui";
import { Building2, Fingerprint } from "lucide-react";
import { useState, type FormEvent } from "react";

type SignupResult = {
  workspace: string;
  display_name: string;
  tenant_status: string;
  enrollment_ticket: string;
  organization: "DEVELOPMENT_UNVERIFIED" | "VERIFICATION_REQUIRED";
};

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
  RATE_LIMITED: "Too many sign-ups from this network. Try again in a few minutes.",
  VALIDATION_FAILED: "Check the details and try again.",
};

/** Register a company: creates its tenant and makes you its administrator. The passkey you create
 * next is your account - nothing else about you is stored. */
export function Register() {
  const [company, setCompany] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [touched, setTouched] = useState(false);
  const [name, setName] = useState("");
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const ws = touched ? workspace : suggestWorkspace(company);
  const wsValid = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/.test(ws);
  const valid = company.trim().length > 0 && wsValid && name.trim().length > 0;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!valid) return;
    setPending(true);
    setFailure(null);
    try {
      const res = await fetch("/api/signup", {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ company_name: company.trim(), workspace: ws }),
      });
      const body = (await res.json().catch(() => ({}))) as Partial<SignupResult> & { error?: { code?: string; message?: string } };
      if (res.status !== 201 || !body.enrollment_ticket) {
        const code = body.error?.code ?? "";
        setFailure(MESSAGES[code] ?? body.error?.message ?? "Registration failed. Please try again.");
        setPending(false);
        return;
      }
      // The ticket is single use; the name only labels the passkey on this account.
      const q = new URLSearchParams({ ticket: body.enrollment_ticket, label: name.trim().slice(0, 60), company: body.display_name ?? "" });
      window.location.assign(`/enroll?${q.toString()}`);
    } catch {
      setFailure("The service is temporarily unavailable. Please try again.");
      setPending(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Register your company</h1>
        <p className="mt-1 text-sm text-ink-muted">Create your company&apos;s workspace. You become its first administrator.</p>
      </div>

      {failure ? (
        <Banner tone="danger" live="assertive" title="Couldn't register">
          {failure}
        </Banner>
      ) : null}

      <Card>
        <form className="space-y-4" onSubmit={(e) => void submit(e)}>
          <TextField label="Company name" isRequired value={company} onChange={setCompany} description="As people in your company know it." />
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
            errorMessage={ws && !wsValid ? "Use lower-case letters, digits and inner hyphens." : undefined}
          />
          <TextField
            label="Your name"
            isRequired
            value={name}
            onChange={setName}
            description="Only used to label your passkey, e.g. “Jane's laptop”."
          />
          <Button type="submit" size="lg" className="w-full" pending={pending} isDisabled={!valid} icon={<Building2 aria-hidden className="size-5" />}>
            Create workspace
          </Button>
        </form>
      </Card>

      <div className="rounded-card border border-border bg-surface p-4 text-sm text-ink-muted">
        <div className="mb-1 flex items-center gap-2 font-medium text-ink">
          <Fingerprint aria-hidden className="size-4" /> What happens next
        </div>
        <ol className="list-decimal space-y-1 pl-5">
          <li>You create a passkey on this device. It is your sign-in; there is no password.</li>
          <li>You land in your company&apos;s console as its administrator.</li>
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
