"use client";

import { MoreHorizontal, X } from "lucide-react";
import type { ReactNode } from "react";
import {
  Dialog as AriaDialog,
  Heading,
  Menu as AriaMenu,
  MenuItem,
  MenuTrigger,
  Modal,
  ModalOverlay,
  Popover,
  Separator,
  Tab,
  TabList,
  TabPanel,
  Tabs as AriaTabs,
  Tooltip as AriaTooltip,
  TooltipTrigger,
  Button as AriaButton,
  type Key,
} from "react-aria-components";

import { cn } from "../utils/cn";

export function Dialog({
  isOpen,
  onOpenChange,
  title,
  description,
  children,
  footer,
  size = "md",
  isDismissable = true,
  role = "dialog",
}: {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
  isDismissable?: boolean;
  role?: "dialog" | "alertdialog";
}) {
  return (
    <ModalOverlay
      isOpen={isOpen}
      onOpenChange={onOpenChange}
      isDismissable={isDismissable}
      isKeyboardDismissDisabled={!isDismissable}
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4 entering:animate-fade-in"
    >
      <Modal
        className={cn(
          "flex max-h-[90dvh] w-full flex-col overflow-hidden rounded-xl border border-border bg-raised shadow-pop",
          { sm: "max-w-md", md: "max-w-lg", lg: "max-w-2xl", xl: "max-w-4xl" }[size],
        )}
      >
        <AriaDialog role={role} className="flex min-h-0 flex-1 flex-col outline-none">
          <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
            <div className="min-w-0">
              <Heading slot="title" className="text-base font-semibold text-ink">
                {title}
              </Heading>
              {description ? <p className="mt-0.5 text-sm text-ink-muted">{description}</p> : null}
            </div>
            {isDismissable ? (
              <AriaButton slot="close" aria-label="Close" className="rounded p-1 text-ink-subtle outline-none hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-brand-500">
                <X aria-hidden className="size-4" />
              </AriaButton>
            ) : null}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-5">{children}</div>
          {footer ? <div className="flex flex-wrap justify-end gap-2 border-t border-border bg-canvas/50 px-5 py-3">{footer}</div> : null}
        </AriaDialog>
      </Modal>
    </ModalOverlay>
  );
}

/** Quick inspect side panel; deep edit navigates to the full object page (§4 "Side panel"). */
export function SidePanel({
  isOpen,
  onOpenChange,
  title,
  subtitle,
  children,
  footer,
}: {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <ModalOverlay isOpen={isOpen} onOpenChange={onOpenChange} isDismissable className="fixed inset-0 z-50 bg-scrim entering:animate-fade-in">
      <Modal className="fixed inset-y-0 end-0 flex w-full max-w-lg flex-col border-s border-border bg-raised shadow-pop entering:animate-slide-in-end">
        <AriaDialog className="flex min-h-0 flex-1 flex-col outline-none">
          <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
            <div className="min-w-0">
              <Heading slot="title" className="text-base font-semibold text-ink">
                {title}
              </Heading>
              {subtitle ? <div className="mt-1">{subtitle}</div> : null}
            </div>
            <AriaButton slot="close" aria-label="Close panel" className="rounded p-1 text-ink-subtle outline-none hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-brand-500">
              <X aria-hidden className="size-4" />
            </AriaButton>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-5">{children}</div>
          {footer ? <div className="flex flex-wrap gap-2 border-t border-border px-5 py-3">{footer}</div> : null}
        </AriaDialog>
      </Modal>
    </ModalOverlay>
  );
}

export type MenuAction = {
  id: string;
  label: string;
  icon?: ReactNode;
  danger?: boolean;
  disabled?: boolean;
  href?: string;
  separatorBefore?: boolean;
};

/** Overflow/action menu. Destructive items open their own reviewed flow; never act directly. */
export function ActionMenu({
  label,
  items,
  onAction,
  trigger,
}: {
  label: string;
  items: MenuAction[];
  onAction?: (id: string) => void;
  trigger?: ReactNode;
}) {
  return (
    <MenuTrigger>
      {trigger ?? (
        <AriaButton aria-label={label} className="inline-flex size-8 items-center justify-center rounded-control text-ink-muted outline-none hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-brand-500">
          <MoreHorizontal aria-hidden className="size-4" />
        </AriaButton>
      )}
      <Popover placement="bottom end" className="min-w-48 rounded-lg border border-border bg-raised p-1 shadow-pop entering:animate-fade-in">
        <AriaMenu aria-label={label} onAction={(k: Key) => onAction?.(String(k))} className="outline-none" disabledKeys={items.filter((i) => i.disabled).map((i) => i.id)}>
          {items.flatMap((it) => [
            ...(it.separatorBefore ? [<Separator key={`${it.id}-sep`} className="my-1 border-t border-border" />] : []),
            <MenuItem
              key={it.id}
              id={it.id}
              href={it.href}
              textValue={it.label}
              className={cn(
                "flex cursor-default items-center gap-2 rounded-md px-3 py-1.5 text-sm outline-none focus:bg-hover data-disabled:opacity-50",
                it.danger ? "text-danger-fg" : "text-ink",
              )}
            >
              {it.icon}
              {it.label}
            </MenuItem>,
          ])}
        </AriaMenu>
      </Popover>
    </MenuTrigger>
  );
}

export function Tabs({
  tabs,
  defaultTab,
  selectedTab,
  onSelectionChange,
  label,
}: {
  tabs: { id: string; label: ReactNode; content: ReactNode; count?: number }[];
  defaultTab?: string;
  selectedTab?: string;
  onSelectionChange?: (id: string) => void;
  label: string;
}) {
  return (
    <AriaTabs
      defaultSelectedKey={selectedTab === undefined ? (defaultTab ?? tabs[0]?.id) : undefined}
      selectedKey={selectedTab}
      onSelectionChange={(k) => onSelectionChange?.(String(k))}
    >
      <TabList aria-label={label} className="mb-5 flex gap-1 overflow-x-auto border-b border-border">
        {tabs.map((t) => (
          <Tab
            key={t.id}
            id={t.id}
            className="-mb-px flex cursor-default items-center gap-1.5 whitespace-nowrap border-b-2 border-transparent px-3 py-2 text-sm font-medium text-ink-muted outline-none hover:text-ink selected:border-brand-600 selected:text-brand-700 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500"
          >
            {t.label}
            {t.count !== undefined ? <span className="tabular rounded-full bg-sunken px-1.5 text-2xs text-ink-muted">{t.count}</span> : null}
          </Tab>
        ))}
      </TabList>
      {tabs.map((t) => (
        <TabPanel key={t.id} id={t.id} className="outline-none">
          {t.content}
        </TabPanel>
      ))}
    </AriaTabs>
  );
}

export function Tooltip({ content, children }: { content: ReactNode; children: ReactNode }) {
  return (
    <TooltipTrigger delay={400}>
      {children}
      <AriaTooltip offset={6} className="max-w-xs rounded-md bg-code-bg px-2 py-1 text-xs text-code-fg shadow-pop entering:animate-fade-in">
        {content}
      </AriaTooltip>
    </TooltipTrigger>
  );
}
