"use client";

import type { TenantView } from "@zoiko/api-client";
import { Banner, Card } from "@zoiko/ui";
import { BellRing, CheckCircle2, Fingerprint, KeyRound, LifeBuoy, Link2, MonitorSmartphone, ScanFace, ShieldCheck, UserRound } from "lucide-react";
import type { ComponentType, ReactNode } from "react";

import { useCellQuery } from "@/lib/cell";
import { UserText } from "@/lib/user-text";

/** What a plain member does here: their own account. Everything on this page is the member's own data (own passkeys, sessions, alerts), the one kind of
 * read the cell gives every signed-in person; company administration is never offered, so nothing here depends on a role the member does not hold. */
const LINKS: { href: string; label: string; hint: string; icon: ComponentType<{ className?: string; "aria-hidden"?: boolean }> }[] = [
  { href: "/account/passkeys", label: "Passkeys", hint: "Sign in with this device", icon: Fingerprint },
  { href: "/account/sessions", label: "Sessions", hint: "Where you are signed in", icon: MonitorSmartphone },
  { href: "/account/recovery", label: "Recovery codes", hint: "A way back if you lose a device", icon: LifeBuoy },
  { href: "/account/connected-apps", label: "Connected apps", hint: "Apps you allowed", icon: Link2 },
  { href: "/account/verification", label: "Identity verification", hint: "Your verification level", icon: ScanFace },
  { href: "/account/privacy", label: "Privacy", hint: "Your choices and requests", icon: ShieldCheck },
];

function Tile({ href, label, hint, icon: Icon }: (typeof LINKS)[number]) {
  return (
    <a
      href={href}
      className="flex min-h-16 items-center gap-3 rounded-lg border border-border bg-surface p-3 hover:bg-hover focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:outline-none"
    >
      <span className="grid size-9 shrink-0 place-items-center rounded-md bg-brand-50 text-brand-700">
        <Icon aria-hidden className="size-4" />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-medium text-ink">{label}</span>
        <span className="block text-xs text-ink-muted">{hint}</span>
      </span>
    </a>
  );
}

/** How the person gets in: a passkey on a device (best), or only the company login. Says plainly which it is, and what to add. */
function SignInCard() {
  const passkeys = useCellQuery(["authn", "authenticators"], (api) => api.me.authenticators());
  const codes = useCellQuery(["authn", "recovery-status"], (api) => api.me.recoveryCodeStatus());
  if (!passkeys.data) return null;
  const active = passkeys.data.items.filter((a) => a.status === "ACTIVE").length;
  const left = codes.data?.codes_remaining ?? 0;
  if (active === 0) {
    return (
      <Banner tone="info" icon={KeyRound} title="You sign in with your company account">
        You can also add a passkey on this device, so you can sign in without going through your company login.{" "}
        <a href="/account/passkeys" className="font-medium text-brand-700 hover:underline">
          Add a passkey
        </a>
      </Banner>
    );
  }
  if (active === 1 && left === 0) {
    return (
      <Banner tone="warn" icon={KeyRound} title="Protect your access">
        You have one passkey and no recovery codes. If you lose this device you will need an emailed link or your administrator to get back in. Add a second passkey or save
        recovery codes now.{" "}
        <a href="/account/recovery" className="font-medium text-brand-700 hover:underline">
          Save recovery codes
        </a>
      </Banner>
    );
  }
  return (
    <Banner tone="ok" icon={CheckCircle2} title="Your sign-in is protected">
      {active} passkey{active === 1 ? "" : "s"} registered{left > 0 ? ` and ${left} recovery code${left === 1 ? "" : "s"} saved` : ""}.
    </Banner>
  );
}

function OpenAlerts() {
  const summary = useCellQuery(["alerts", "summary", "member"], (api) => api.alerts.summary());
  const open = summary.data?.open ?? 0;
  return (
    <a
      href="/alerts"
      className="flex min-h-16 items-center gap-3 rounded-lg border border-border bg-surface p-3 hover:bg-hover focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:outline-none"
    >
      <span className="grid size-9 shrink-0 place-items-center rounded-md bg-brand-50 text-brand-700">
        <BellRing aria-hidden className="size-4" />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-medium text-ink">Alerts &amp; tasks</span>
        <span className="block text-xs text-ink-muted">{open > 0 ? `${open} open` : "Nothing needs your attention"}</span>
      </span>
    </a>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="space-y-3">
      <h2 className="text-sm font-semibold text-ink">{title}</h2>
      {children}
    </section>
  );
}

/** The home page of a plain member (the role someone gets by signing in through the company's login): who they are here, how they sign in, their own account, and how
 * to ask for more. It shows no company administration and no company data the member's role cannot read. */
export function MemberHome({ tenant, roleLabel }: { tenant: TenantView; roleLabel: string }) {
  return (
    <div className="space-y-6">
      <Card>
        <div className="flex items-start gap-3">
          <span className="grid size-10 shrink-0 place-items-center rounded-full bg-brand-50 text-brand-700">
            <UserRound aria-hidden className="size-5" />
          </span>
          <div className="min-w-0 space-y-1">
            <p className="text-sm text-ink-muted">
              You are signed in to <span className="font-medium text-ink"><UserText>{tenant.display_name}</UserText></span> as
            </p>
            <p className="inline-flex min-h-7 items-center rounded-full border border-border px-2.5 text-xs font-medium text-ink">{roleLabel}</p>
            <p className="pt-1 text-sm text-ink-muted">Members use their own account here. The company&apos;s owners manage everything else.</p>
          </div>
        </div>
      </Card>
      <SignInCard />
      <Section title="Your account">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <OpenAlerts />
          {LINKS.map((l) => (
            <Tile key={l.href} {...l} />
          ))}
        </div>
      </Section>
      <Card title="Need more access?" description="An administrator role is never granted automatically.">
        <p className="text-sm text-ink-muted">
          Ask one of the company&apos;s owners. They can invite you with a role, and the invitation is emailed to you.
        </p>
      </Card>
    </div>
  );
}
