"use client";

/** Global application shell (ZID-UX-001 §3, §3.1, §32).
 *
 * - Tenant and environment are always visible; switching asks first when there is unsaved work.
 * - Production carries a persistent hatched stripe + text label, not color alone (UX-04).
 * - Navigation is permission-aware for clarity only; the backend remains authoritative (§2).
 * - ≥1024px: sidebar (collapsible). <1024px: drawer navigation. */
import {
  Bell,
  ChevronDown,
  ChevronsLeft,
  ChevronsRight,
  CircleHelp,
  LogOut,
  Menu as MenuIcon,
  Search,
  X,
  type LucideIcon,
} from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Dialog as AriaDialog,
  DialogTrigger,
  Link as AriaLink,
  Menu as AriaMenu,
  MenuItem,
  MenuTrigger,
  Modal,
  ModalOverlay,
  Popover,
  Separator,
  Button as AriaButton,
} from "react-aria-components";

import { EnvironmentBadge, type EnvironmentKind } from "../components/badges";
import { Button } from "../components/button";
import { Dialog } from "../components/overlay";
import { cn } from "../utils/cn";
import { CommandPalette, type Command, type SearchProvider } from "./command-palette";

/* ---------------------------------------------------------------------------------------------- */

type UnsavedApi = { dirty: boolean; setDirty: (key: string, dirty: boolean) => void };
const UnsavedContext = createContext<UnsavedApi>({ dirty: false, setDirty: () => undefined });

/** Forms register unsaved state so tenant/environment switches and sign-out can warn (§3.1). */
export function useUnsavedChanges(key: string, dirty: boolean) {
  const { setDirty } = useContext(UnsavedContext);
  useEffect(() => {
    setDirty(key, dirty);
    return () => setDirty(key, false);
  }, [key, dirty, setDirty]);
}

/* ---------------------------------------------------------------------------------------------- */

export type NavItem = { id: string; label: string; href: string; icon: LucideIcon; badge?: number; badgeTone?: "danger" | "warn" };
export type NavGroup = { label?: string; items: NavItem[] };
export type TenantRef = { id: string; name: string };
export type AlertItem = { id: string; title: string; href: string; severity: "CRITICAL" | "HIGH" | "MEDIUM" | "INFO"; age: string };

export type AppShellProps = {
  surface: string;
  homeHref: string;
  nav: NavGroup[];
  currentPath: string;
  tenant?: TenantRef;
  tenants?: TenantRef[];
  onSwitchTenant?: (id: string) => void;
  environment: EnvironmentKind;
  environments?: EnvironmentKind[];
  onSwitchEnvironment?: (env: EnvironmentKind) => void;
  user: { name: string; email?: string; roles?: string[] };
  alerts?: AlertItem[];
  alertsHref?: string;
  helpHref?: string;
  commands: Command[];
  search?: SearchProvider;
  onNavigate: (href: string) => void;
  onSignOut: () => void;
  statusBar?: ReactNode;
  /** Persistent banners (critical alerts, degraded service). */
  banners?: ReactNode;
  /** Extra marker in the top bar, e.g. Operator JIT elevation. */
  topBarExtra?: ReactNode;
  children: ReactNode;
};

export function AppShell(props: AppShellProps) {
  const [dirtyKeys, setDirtyKeys] = useState<Set<string>>(new Set());
  const setDirty = useCallback((key: string, dirty: boolean) => {
    setDirtyKeys((s) => {
      if (s.has(key) === dirty) return s;
      const n = new Set(s);
      if (dirty) n.add(key);
      else n.delete(key);
      return n;
    });
  }, []);
  const unsaved = useMemo(() => ({ dirty: dirtyKeys.size > 0, setDirty }), [dirtyKeys, setDirty]);

  const [paletteOpen, setPaletteOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [pendingSwitch, setPendingSwitch] = useState<null | { label: string; run: () => void }>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => setDrawerOpen(false), [props.currentPath]);

  useEffect(() => {
    if (!unsaved.dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [unsaved.dirty]);

  const guarded = (label: string, run: () => void) => (unsaved.dirty ? setPendingSwitch({ label, run }) : run());
  const prod = props.environment === "production";

  return (
    <UnsavedContext.Provider value={unsaved}>
      <a
        href="#main"
        className="fixed start-2 top-2 z-[70] -translate-y-20 rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-on-brand focus:translate-y-0"
      >
        Skip to main content
      </a>
      <div className="flex min-h-dvh flex-col">
        {prod ? (
          <div aria-hidden className="env-hatch h-1 shrink-0 text-env-prod" />
        ) : (
          <div aria-hidden className="h-1 shrink-0 border-b border-dashed border-env-sandbox bg-info-bg" />
        )}
        <TopBar
          {...props}
          collapsed={collapsed}
          onOpenDrawer={() => setDrawerOpen(true)}
          onOpenPalette={() => setPaletteOpen(true)}
          guarded={guarded}
        />
        {props.banners ? <div className="space-y-2 border-b border-border bg-surface px-4 py-2 lg:px-6">{props.banners}</div> : null}
        <div className="flex min-h-0 flex-1">
          <aside
            aria-label="Primary"
            className={cn(
              "sticky top-14 hidden h-[calc(100dvh-3.75rem)] shrink-0 flex-col border-e border-border bg-surface lg:flex",
              collapsed ? "w-16" : "w-64",
            )}
          >
            <SideNav nav={props.nav} currentPath={props.currentPath} collapsed={collapsed} />
            <div className="border-t border-border p-2">
              <button
                type="button"
                onClick={() => setCollapsed((c) => !c)}
                aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
                className="flex w-full items-center justify-center gap-2 rounded-md px-2 py-1.5 text-xs text-ink-subtle hover:bg-hover hover:text-ink"
              >
                {collapsed ? <ChevronsRight aria-hidden className="size-4 rtl:rotate-180" /> : <ChevronsLeft aria-hidden className="size-4 rtl:rotate-180" />}
                {collapsed ? null : "Collapse"}
              </button>
            </div>
          </aside>
          <main id="main" tabIndex={-1} className="min-w-0 flex-1 outline-none">
            <div className="mx-auto w-full max-w-[100rem] px-4 py-6 sm:px-6 lg:px-8">{props.children}</div>
          </main>
        </div>
        {props.statusBar ? (
          <footer className="border-t border-border bg-surface px-4 py-1.5 text-xs text-ink-muted lg:px-6">{props.statusBar}</footer>
        ) : null}
      </div>

      <ModalOverlay isOpen={drawerOpen} onOpenChange={setDrawerOpen} isDismissable className="fixed inset-0 z-50 bg-scrim lg:hidden">
        <Modal className="fixed inset-y-0 start-0 flex w-72 max-w-[85vw] flex-col bg-surface shadow-pop">
          <AriaDialog aria-label="Navigation" className="flex min-h-0 flex-1 flex-col outline-none">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <span className="text-sm font-semibold text-ink">{props.surface}</span>
              <AriaButton slot="close" aria-label="Close navigation" className="rounded p-1 text-ink-subtle hover:bg-hover">
                <X aria-hidden className="size-4" />
              </AriaButton>
            </div>
            <SideNav nav={props.nav} currentPath={props.currentPath} collapsed={false} />
          </AriaDialog>
        </Modal>
      </ModalOverlay>

      <CommandPalette
        isOpen={paletteOpen}
        onOpenChange={setPaletteOpen}
        commands={props.commands}
        search={props.search}
        onNavigate={props.onNavigate}
      />

      <Dialog
        isOpen={pendingSwitch !== null}
        onOpenChange={(o) => !o && setPendingSwitch(null)}
        title="Discard unsaved changes?"
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button variant="secondary" onPress={() => setPendingSwitch(null)}>
              Keep editing
            </Button>
            <Button
              variant="danger"
              onPress={() => {
                const run = pendingSwitch?.run;
                setPendingSwitch(null);
                setDirtyKeys(new Set());
                run?.();
              }}
            >
              Discard and {pendingSwitch?.label}
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-muted">
          You have unsaved changes on this page. Switching context discards them; nothing has been submitted.
        </p>
      </Dialog>
    </UnsavedContext.Provider>
  );
}

function isCurrent(currentPath: string, href: string) {
  return currentPath === href || (href !== "/" && currentPath.startsWith(`${href}/`));
}

/** The one menu address that matches the page best: when a parent page (/verify-api) and its child (/verify-api/command-center) are both in the menu, only the child lights up. */
function bestMatch(nav: NavGroup[], currentPath: string): string | null {
  let best: string | null = null;
  for (const g of nav) for (const it of g.items) if (isCurrent(currentPath, it.href) && (best === null || it.href.length > best.length)) best = it.href;
  return best;
}

function SideNav({ nav, currentPath, collapsed }: { nav: NavGroup[]; currentPath: string; collapsed: boolean }) {
  const best = bestMatch(nav, currentPath);
  return (
    <nav aria-label="Main" className="min-h-0 flex-1 overflow-y-auto px-2 py-3">
      {nav.map((g, gi) => (
        <div key={gi} className={cn(gi > 0 && "mt-4")}>
          {g.label && !collapsed ? (
            <div className="px-3 pb-1 text-2xs font-semibold uppercase tracking-wider text-ink-subtle">{g.label}</div>
          ) : gi > 0 ? (
            <div aria-hidden className="mx-3 mb-2 border-t border-border" />
          ) : null}
          <ul className="space-y-0.5">
            {g.items.map((it) => {
              const current = it.href === best;
              const Icon = it.icon;
              return (
                <li key={it.id}>
                  <AriaLink
                    href={it.href}
                    aria-current={current ? "page" : undefined}
                    aria-label={collapsed ? it.label : undefined}
                    className={cn(
                      "group flex items-center gap-3 rounded-md px-3 py-1.5 text-sm font-medium outline-none transition-colors",
                      "text-ink-muted hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-brand-500",
                      "current:bg-brand-50 current:text-brand-700",
                      collapsed && "justify-center px-0",
                    )}
                  >
                    <Icon aria-hidden className="size-4 shrink-0 opacity-80 group-aria-[current=page]:opacity-100" />
                    {collapsed ? null : <span className="min-w-0 flex-1 break-words leading-tight">{it.label}</span>}
                    {it.badge && !collapsed ? (
                      <span
                        className={cn(
                          "tabular rounded-full px-1.5 text-2xs font-semibold",
                          it.badgeTone === "danger" ? "bg-danger-bg text-danger-fg" : it.badgeTone === "warn" ? "bg-warn-bg text-warn-fg" : "bg-sunken text-ink-muted",
                        )}
                      >
                        {it.badge}
                        <span className="sr-only"> items need attention</span>
                      </span>
                    ) : null}
                  </AriaLink>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

function TopBar({
  surface,
  homeHref,
  tenant,
  tenants,
  onSwitchTenant,
  environment,
  environments,
  onSwitchEnvironment,
  user,
  alerts = [],
  alertsHref,
  helpHref,
  onSignOut,
  onOpenDrawer,
  onOpenPalette,
  guarded,
  topBarExtra,
}: AppShellProps & {
  collapsed: boolean;
  onOpenDrawer: () => void;
  onOpenPalette: () => void;
  guarded: (label: string, run: () => void) => void;
}) {
  const critical = alerts.filter((a) => a.severity === "CRITICAL").length;
  return (
    <header className="sticky top-0 z-40 flex h-14 shrink-0 items-center gap-1 border-b border-border bg-surface/95 px-2 backdrop-blur sm:px-3 supports-[backdrop-filter]:bg-surface/85 sm:gap-3 lg:px-4">
      <button
        type="button"
        onClick={onOpenDrawer}
        aria-label="Open navigation"
        className="rounded-md p-1.5 text-ink-muted hover:bg-hover hover:text-ink lg:hidden"
      >
        <MenuIcon aria-hidden className="size-5" />
      </button>
      <AriaLink href={homeHref} aria-label={`ZOIKO iD ${surface}`} className="flex shrink-0 items-center gap-2 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
        <ZoikoMark />
        <span className="hidden flex-col leading-none sm:flex">
          <span className="text-sm font-bold tracking-tight text-ink">
            ZOIKO <span className="text-brand-600">iD</span>
          </span>
          <span className="mt-0.5 text-2xs font-medium text-ink-subtle">{surface}</span>
        </span>
      </AriaLink>

      <div aria-hidden className="mx-1 hidden h-6 w-px bg-border md:block" />

      {tenant ? (
        <MenuTrigger>
          <AriaButton
            aria-label={`Tenant: ${tenant.name}. Switch tenant`}
            className="hidden min-w-0 items-center gap-1.5 rounded-md px-2 py-1 text-sm outline-none hover:bg-hover focus-visible:ring-2 focus-visible:ring-brand-500 md:flex"
          >
            <span className="text-ink-subtle">Tenant</span>
            <span className="max-w-40 truncate font-semibold text-ink">{tenant.name}</span>
            <ChevronDown aria-hidden className="size-3.5 text-ink-subtle" />
          </AriaButton>
          <Popover placement="bottom start" className="min-w-56 rounded-lg border border-border bg-raised p-1 shadow-pop">
            <AriaMenu
              aria-label="Switch tenant"
              onAction={(k) => {
                const t = tenants?.find((x) => x.id === k);
                if (t && t.id !== tenant.id) guarded(`switch to ${t.name}`, () => onSwitchTenant?.(t.id));
              }}
              className="outline-none"
            >
              {(tenants ?? [tenant]).map((t) => (
                <MenuItem key={t.id} id={t.id} className="flex cursor-default flex-col rounded-md px-3 py-1.5 text-sm text-ink outline-none focus:bg-hover">
                  <span className={cn(t.id === tenant.id && "font-semibold")}>{t.name}</span>
                  <span className="font-mono text-2xs text-ink-subtle">{t.id}</span>
                </MenuItem>
              ))}
            </AriaMenu>
          </Popover>
        </MenuTrigger>
      ) : null}

      {environments && environments.length > 1 ? (
        <MenuTrigger>
          <AriaButton aria-label={`Environment: ${environment}. Switch environment`} className="flex items-center gap-1 rounded-md p-0.5 outline-none hover:bg-hover focus-visible:ring-2 focus-visible:ring-brand-500">
            <EnvironmentBadge environment={environment} />
            <ChevronDown aria-hidden className="size-3.5 text-ink-subtle" />
          </AriaButton>
          <Popover placement="bottom start" className="min-w-44 rounded-lg border border-border bg-raised p-1 shadow-pop">
            <AriaMenu
              aria-label="Switch environment"
              onAction={(k) => {
                const env = k as EnvironmentKind;
                if (env !== environment) guarded(`switch to ${env}`, () => onSwitchEnvironment?.(env));
              }}
              className="outline-none"
            >
              {environments.map((e) => (
                <MenuItem key={e} id={e} className="cursor-default rounded-md px-2 py-1.5 outline-none focus:bg-hover">
                  <EnvironmentBadge environment={e} />
                </MenuItem>
              ))}
            </AriaMenu>
          </Popover>
        </MenuTrigger>
      ) : (
        <EnvironmentBadge environment={environment} />
      )}

      {topBarExtra}

      <div className="flex-1" />

      <button
        type="button"
        onClick={onOpenPalette}
        className="hidden h-8 w-64 items-center gap-2 rounded-control border border-border bg-canvas px-2.5 text-sm text-ink-subtle outline-none hover:border-border-strong focus-visible:ring-2 focus-visible:ring-brand-500 md:flex"
      >
        <Search aria-hidden className="size-4" />
        <span className="flex-1 text-start">Search…</span>
        <kbd className="rounded border border-border bg-surface px-1.5 font-mono text-2xs">Ctrl K</kbd>
      </button>
      <button type="button" onClick={onOpenPalette} aria-label="Search" className="rounded-md p-1.5 text-ink-muted hover:bg-hover md:hidden">
        <Search aria-hidden className="size-5" />
      </button>

      <DialogTrigger>
        <AriaButton
          aria-label={`Alerts, ${alerts.length} open${critical ? `, ${critical} critical` : ""}`}
          className="relative rounded-md p-1.5 text-ink-muted outline-none hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-brand-500"
        >
          <Bell aria-hidden className="size-5" />
          {alerts.length ? (
            <span
              aria-hidden
              className={cn(
                "absolute -end-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-2xs font-bold text-white",
                critical ? "bg-danger-solid" : "bg-brand-600",
              )}
            >
              {alerts.length}
            </span>
          ) : null}
        </AriaButton>
        <Popover placement="bottom end" className="w-96 max-w-[calc(100vw-1rem)] rounded-lg border border-border bg-raised shadow-pop">
          <AriaDialog aria-label="Alerts and tasks" className="outline-none">
            <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
              <span className="text-sm font-semibold text-ink">Alerts & tasks</span>
              {alertsHref ? (
                <AriaLink href={alertsHref} className="text-xs font-medium text-brand-600 hover:underline">
                  View all
                </AriaLink>
              ) : null}
            </div>
            {alerts.length === 0 ? (
              <p className="px-4 py-6 text-center text-sm text-ink-muted">No open alerts.</p>
            ) : (
              <ul className="max-h-96 divide-y divide-border overflow-y-auto">
                {alerts.map((a) => (
                  <li key={a.id}>
                    <AriaLink href={a.href} className="flex items-start gap-3 px-4 py-2.5 outline-none hover:bg-hover focus-visible:bg-hover">
                      <span
                        className={cn(
                          "mt-0.5 rounded px-1.5 text-2xs font-bold uppercase",
                          a.severity === "CRITICAL" && "bg-danger-bg text-danger-fg",
                          a.severity === "HIGH" && "bg-warn-bg text-warn-fg",
                          a.severity === "MEDIUM" && "bg-info-bg text-info-fg",
                          a.severity === "INFO" && "bg-neutral-bg text-neutral-fg",
                        )}
                      >
                        {a.severity}
                      </span>
                      <span className="min-w-0 flex-1 text-sm text-ink">{a.title}</span>
                      <span className="shrink-0 text-xs text-ink-subtle">{a.age}</span>
                    </AriaLink>
                  </li>
                ))}
              </ul>
            )}
          </AriaDialog>
        </Popover>
      </DialogTrigger>

      {helpHref ? (
        <AriaLink href={helpHref} aria-label="Help and support" className="hidden rounded-md p-1.5 text-ink-muted outline-none hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-brand-500 sm:block">
          <CircleHelp aria-hidden className="size-5" />
        </AriaLink>
      ) : null}

      <MenuTrigger>
        <AriaButton aria-label={`Account: ${user.name}`} className="flex items-center gap-1.5 rounded-full outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
          <span className="flex size-8 items-center justify-center rounded-full bg-brand-100 text-xs font-semibold text-brand-700">{initials(user.name)}</span>
        </AriaButton>
        <Popover placement="bottom end" className="min-w-60 rounded-lg border border-border bg-raised p-1 shadow-pop">
          <div className="px-3 py-2">
            <div className="text-sm font-semibold text-ink">{user.name}</div>
            {user.email ? <div className="text-xs text-ink-muted">{user.email}</div> : null}
            {user.roles?.length ? (
              <div className="mt-1.5 flex flex-wrap gap-1">
                {user.roles.map((r) => (
                  <span key={r} className="rounded bg-sunken px-1.5 py-px font-mono text-2xs text-ink-muted">
                    {r}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
          <Separator className="my-1 border-t border-border" />
          <AriaMenu aria-label="Account" onAction={(k) => k === "signout" && guarded("sign out", onSignOut)} className="outline-none">
            <MenuItem id="signout" className="flex cursor-default items-center gap-2 rounded-md px-3 py-1.5 text-sm text-ink outline-none focus:bg-hover">
              <LogOut aria-hidden className="size-4" /> Sign out
            </MenuItem>
          </AriaMenu>
        </Popover>
      </MenuTrigger>
    </header>
  );
}

function initials(name: string) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join("");
}

export function ZoikoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden className={cn("size-8 shrink-0", className)}>
      <rect width="32" height="32" rx="8" className="fill-brand-600" />
      <path d="M9 10.5h14l-10.5 11H23" fill="none" stroke="white" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="23.5" cy="10.5" r="1.6" className="fill-brand-200" />
    </svg>
  );
}

/** Status bar content: "Cell/Residency: EU-1 • Service healthy • Last policy publication 4m ago". */
export function StatusBarItems({ items }: { items: { label: string; value: ReactNode }[] }) {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5">
          <span className="text-ink-subtle">{i.label}</span>
          <span className="font-medium text-ink-muted">{i.value}</span>
        </li>
      ))}
    </ul>
  );
}
