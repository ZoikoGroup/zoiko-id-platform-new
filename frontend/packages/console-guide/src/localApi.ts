/** An in-browser implementation of the assistant widget's API (the same wire contract as the public assistant), backed by the guide
 * engine. It is handed to `<AssistantWidget fetchImpl={...}>`: the widget's UI, focus handling, escaped rendering and accessibility are
 * reused unchanged, while every request is answered HERE. The only things it ever loads are the two read-only navigation endpoints
 * (through the injected loaders); the person's question is never sent anywhere, stored or logged. */

import { answerQuestion, looksSensitive, STARTERS } from "./engine";
import { PAGES, linkId } from "./pages";
import type { GuideAnswer, MyPermissions, RoleMatrix } from "./types";

export interface GuideLoaders {
  loadMatrix(): Promise<RoleMatrix>;
  loadPermissions(): Promise<MyPermissions>;
}

export interface GuideFetchOptions {
  /** The widget's `apiBase`; defaults to "/guide". */
  base?: string;
  now?: () => number;
  /** How long the two catalogues are reused before they are fetched again (milliseconds). */
  cacheMs?: number;
}

const POLICY_VERSION = "console-guide-1";
const IDLE_SECONDS = 1800;
const MAX_CHARS = 500;
const NOTICE =
  "This guide explains your role and the console. It can't see your company's data or change anything, and your questions stay in this browser. Don't type passwords or codes.";

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });

const failure = (status: number, code: string, message: string): Response =>
  json({ error: { code, message, correlation_id: null, retry_after_s: null } }, status);

function responseId(): string {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  return `rsp_${[...b].map((x) => x.toString(16).padStart(2, "0")).join("")}`;
}

export function toPacket(a: GuideAnswer, matrix: RoleMatrix, now: number) {
  const hash = matrix.policy_hash.slice(0, 12);
  const first = a.actions[0];
  return {
    response_id: responseId(),
    policy_version: POLICY_VERSION,
    snapshot_id: `matrix-${hash}`,
    decision: a.decision,
    intent_id: null,
    answer_text: a.text,
    claims: [],
    citations: first ? [{ source_id: "ROLE-MATRIX", revision: hash, title: "Published role matrix", span_id: first, link_id: null }] : [],
    capabilities: [],
    actions: a.links.map((l) => ({ type: "OPEN_APPROVED_LINK", link_id: l.id, label: l.label })),
    safety: { reason: a.decision === "REFUSE" ? "OUT_OF_SCOPE" : "OK", sensitive_input_blocked: false, sample_content: false, ai_assisted: false },
    session_expires_at: new Date(now + IDLE_SECONDS * 1000).toISOString(),
  };
}

export function createGuideFetch(loaders: GuideLoaders, options: GuideFetchOptions = {}): typeof fetch {
  const base = (options.base ?? "/guide").replace(/\/$/, "");
  const now = options.now ?? (() => Date.now());
  const cacheMs = options.cacheMs ?? 30_000;
  let cache: { at: number; matrix: RoleMatrix; perms: MyPermissions } | null = null;

  async function load() {
    if (cache && now() - cache.at < cacheMs) return cache;
    const [matrix, perms] = await Promise.all([loaders.loadMatrix(), loaders.loadPermissions()]);
    cache = { at: now(), matrix, perms };
    return cache;
  }

  const handler = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://local.invalid");
    const method = (init?.method ?? (typeof input === "object" && "method" in input ? input.method : "GET")).toUpperCase();
    if (!url.pathname.startsWith(`${base}/`)) return failure(404, "NOT_FOUND", "Not found.");
    const route = url.pathname.slice(base.length + 1);

    try {
      if (route === "bootstrap" && method === "GET") {
        const { matrix, perms } = await load();
        return json({
          enabled: true,
          display_name: "Console guide",
          locale: "en-US",
          revision: POLICY_VERSION,
          snapshot_id: `matrix-${matrix.policy_hash.slice(0, 12)}`,
          sample_content: false,
          flags: { ai_answers: false, intake_sales: false, intake_support: false, marketing: false },
          limits: { message_max_chars: MAX_CHARS, session_idle_seconds: IDLE_SECONDS },
          starters: STARTERS(perms.roles),
          static_help: ["overview", "account", "alerts", "team"].map((id) => ({ link_id: linkId(id), label: PAGES.find((p) => p.id === id)?.label ?? id })),
          notice: NOTICE,
        });
      }
      if (route === "sessions" && method === "POST") {
        return json({ session_expires_at: new Date(now() + IDLE_SECONDS * 1000).toISOString(), idle_seconds: IDLE_SECONDS }, 201);
      }
      if (route === "sessions" && method === "DELETE") return new Response(null, { status: 204 });
      if (route === "feedback" && method === "POST") return new Response(null, { status: 204 }); // nothing is recorded
      if (route === "intake/config" && method === "GET") {
        const off = { available: false, form_revision: null };
        return json({ sales: off, support: off, marketing: { available: false } });
      }
      if (route === "messages" && method === "POST") {
        let body: unknown;
        try {
          body = JSON.parse(typeof init?.body === "string" ? init.body : "{}");
        } catch {
          return failure(400, "INVALID_REQUEST", "The request is not valid.");
        }
        const text = (body as { text?: unknown } | null)?.text;
        if (typeof text !== "string" || text.trim().length === 0 || text.length > MAX_CHARS) return failure(400, "INVALID_REQUEST", "The request is not valid.");
        if (looksSensitive(text)) {
          return failure(422, "SENSITIVE_CONTENT_BLOCKED", "That looks like it contains a secret or personal data, so it was not processed or stored. Please remove it and try again.");
        }
        const { matrix, perms } = await load();
        return json(toPacket(answerQuestion(text, { matrix, roles: perms.roles, enforced: perms.enforcement }), matrix, now()));
      }
    } catch {
      // the two catalogues could not be loaded (signed out, cell down): the widget shows its own "unavailable" state and static links
      return failure(503, "UNAVAILABLE", "The guide is unavailable right now.");
    }
    return failure(404, "NOT_FOUND", "Not found.");
  };
  return handler as typeof fetch;
}
