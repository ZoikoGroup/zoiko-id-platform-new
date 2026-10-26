"use client";

import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import {
  Button as AriaButton,
  Link as AriaLink,
  type ButtonProps as AriaButtonProps,
  type LinkProps as AriaLinkProps,
} from "react-aria-components";

import { cn } from "../utils/cn";

const base =
  "inline-flex select-none items-center justify-center rounded-[var(--radius-control)] font-medium whitespace-normal text-center transition-colors " +
  "outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-surface " +
  "disabled:cursor-not-allowed disabled:opacity-50 data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50";

const variants = {
  primary: "bg-brand-600 text-on-brand shadow-sm hover:bg-brand-700 pressed:bg-brand-900",
  secondary: "bg-surface text-ink border border-border-strong shadow-sm hover:bg-hover pressed:bg-sunken",
  ghost: "text-ink-muted hover:bg-hover hover:text-ink pressed:bg-sunken",
  danger: "bg-danger-solid text-white shadow-sm hover:bg-danger-fg pressed:bg-danger-fg",
  "danger-outline": "border border-danger-fg/40 bg-surface text-danger-fg hover:bg-danger-bg",
  link: "h-auto px-0 text-brand-600 underline-offset-4 hover:underline",
} as const;

const sizes = {
  // min-height, not height: a long translated label wraps onto a second line instead of spilling or being cut
  xs: "min-h-7 px-2 py-0.5 text-xs gap-1",
  sm: "min-h-8 px-3 py-1 text-sm gap-1.5",
  md: "min-h-9 px-4 py-1.5 text-sm gap-2",
  lg: "min-h-11 px-5 py-2 text-base gap-2",
} as const;

export type ButtonVariant = keyof typeof variants;
export type ButtonSize = keyof typeof sizes;

export function buttonClass(variant: ButtonVariant = "primary", size: ButtonSize = "md", className?: string) {
  return cn(base, variants[variant], variant === "link" ? "" : sizes[size], className);
}

export type ButtonProps = Omit<AriaButtonProps, "children" | "className"> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  pending?: boolean;
  icon?: ReactNode;
  iconEnd?: ReactNode;
  className?: string;
  children?: ReactNode;
};

export function Button({
  variant = "primary",
  size = "md",
  pending = false,
  icon,
  iconEnd,
  className,
  children,
  isDisabled,
  ...props
}: ButtonProps) {
  return (
    <AriaButton {...props} isDisabled={isDisabled || pending} className={buttonClass(variant, size, className)}>
      {pending ? <Loader2 aria-hidden className="size-4 animate-spin" /> : icon}
      {children}
      {iconEnd}
    </AriaButton>
  );
}

/** Navigation styled as a button. Routes through the app's RouterProvider (client-side). */
export function ButtonLink({
  variant = "secondary",
  size = "md",
  icon,
  className,
  children,
  ...props
}: Omit<AriaLinkProps, "className" | "children"> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: ReactNode;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <AriaLink {...props} className={buttonClass(variant, size, className)}>
      {icon}
      {children}
    </AriaLink>
  );
}

/** Icon-only button. `label` is required: there is no unlabeled control in this system (§23). */
export function IconButton({
  label,
  icon,
  variant = "ghost",
  size = "sm",
  className,
  ...props
}: Omit<AriaButtonProps, "children" | "className"> & {
  label: string;
  icon: ReactNode;
  variant?: ButtonVariant;
  size?: ButtonSize;
  className?: string;
}) {
  const square = { xs: "size-7", sm: "size-8", md: "size-9", lg: "size-11" }[size];
  return (
    <AriaButton {...props} aria-label={label} className={cn(buttonClass(variant, size), "px-0", square, className)}>
      {icon}
    </AriaButton>
  );
}

/** Inline text link (routes through RouterProvider). */
export function TextLink({ className, ...props }: AriaLinkProps & { className?: string }) {
  return (
    <AriaLink
      {...props}
      className={cn(
        "rounded-sm font-medium text-brand-600 underline-offset-4 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-brand-500",
        className,
      )}
    />
  );
}
