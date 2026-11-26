import type { VerifyApiApplication, VerifyApiSettingsIn } from "@zoiko/api-client";

import { allowlistProblem, parseAllowlist } from "./credential-options-logic";
import { firstOriginProblem, parseOrigins } from "./verify-api-logic";

/** The plain-language side of an application's workflow and hosted-page settings (CR-156). The cell checks everything again. */

export const WORKFLOWS: { id: string; label: string; help: string }[] = [
  { id: "wf_document_selfie", label: "ID document and selfie", help: "Checks the document, then matches the person's face to it." },
  { id: "wf_document_only", label: "ID document only", help: "Checks the document without a selfie." },
];

/** The approved looks for the verification page. A fixed set: the page stays recognisably a Zoiko iD verification. */
export const THEMES: { id: string; label: string; swatch: string }[] = [
  { id: "blue", label: "Blue", swatch: "bg-sky-600" },
  { id: "green", label: "Green", swatch: "bg-emerald-600" },
  { id: "violet", label: "Violet", swatch: "bg-violet-600" },
  { id: "slate", label: "Slate", swatch: "bg-slate-600" },
];

export const workflowLabel = (id: string): string => WORKFLOWS.find((w) => w.id === id)?.label ?? id.replace(/^wf_/, "").replace(/_/g, " ");

export type SettingsDraft = {
  workflows: string[]; // the ticked ones
  defaultWorkflow: string; // "" = the platform default
  displayName: string;
  theme: string;
  supportUrl: string;
  locale: string;
  origins: string;
  ipAllowlist: string; // one per line; empty = no restriction
};

/** The form as the application stands. An empty allowed list means every workflow, so every box starts ticked. */
export function toDraft(a: VerifyApiApplication): SettingsDraft {
  return {
    workflows: a.allowed_workflows.length ? [...a.allowed_workflows] : WORKFLOWS.map((w) => w.id),
    defaultWorkflow: a.default_workflow_id ?? "",
    displayName: a.branding.display_name ?? "",
    theme: a.branding.theme ?? "",
    supportUrl: a.branding.support_url ?? "",
    locale: a.branding.locale ?? "",
    origins: a.redirect_origins.join("\n"),
    ipAllowlist: a.ip_allowlist.join("\n"),
  };
}

/** Why the settings cannot be saved yet, or null. */
export function draftProblem(d: SettingsDraft): string | null {
  if (d.workflows.length === 0) return "Allow at least one workflow.";
  if (d.defaultWorkflow && !d.workflows.includes(d.defaultWorkflow)) return "The default workflow must be one of the allowed ones.";
  const name = d.displayName.trim();
  if (name && (name.length < 2 || name.length > 60 || /[<>]/.test(name))) return "The name customers see is 2 to 60 characters, without < or >.";
  const url = d.supportUrl.trim();
  if (url) {
    try {
      const u = new URL(url);
      if (u.protocol !== "https:" || u.username || u.password || u.hash) return "The help link must be an https address without a login.";
    } catch {
      return "The help link is not a web address. Use something like https://example.com/help.";
    }
  }
  if (d.locale.trim() && !/^[a-z]{2}(-[A-Z]{2})?$/.test(d.locale.trim())) return "The language is like en or en-GB.";
  return allowlistProblem(parseAllowlist(d.ipAllowlist)) ?? firstOriginProblem(parseOrigins(d.origins));
}

/** The request. Every workflow ticked is sent as an empty list ("every approved workflow"), so a workflow added later is allowed too. */
export function toSettings(d: SettingsDraft): VerifyApiSettingsIn {
  const all = WORKFLOWS.every((w) => d.workflows.includes(w.id));
  const branding: NonNullable<VerifyApiSettingsIn["branding"]> = {};
  if (d.displayName.trim()) branding.display_name = d.displayName.trim();
  if (d.theme) branding.theme = d.theme;
  if (d.supportUrl.trim()) branding.support_url = d.supportUrl.trim();
  if (d.locale.trim()) branding.locale = d.locale.trim();
  return {
    redirect_origins: parseOrigins(d.origins),
    allowed_workflows: all ? [] : [...d.workflows].sort(),
    default_workflow_id: d.defaultWorkflow,
    branding,
    ip_allowlist: parseAllowlist(d.ipAllowlist),
  };
}
