import { vi } from "vitest";

import type { AnswerPacket, Bootstrap } from "../src";

export const BASE = "/api/public/chat/v1";

export const BOOT: Bootstrap = {
  enabled: true,
  display_name: "Zoiko iD Assistant",
  locale: "en-US",
  revision: "pub-policy-1",
  snapshot_id: "sample-test",
  sample_content: true,
  flags: { ai_answers: false, intake_sales: true, intake_support: true, marketing: false },
  limits: { message_max_chars: 1000, session_idle_seconds: 1800 },
  starters: [{ label: "What is Zoiko iD?", text: "What is Zoiko iD?" }],
  static_help: [{ link_id: "OVERVIEW", label: "Platform overview" }],
  notice: "Do not enter passwords, one-time codes, identity documents or personal records.",
};

export function packet(over: Partial<AnswerPacket> = {}): AnswerPacket {
  return {
    response_id: "rsp_1",
    policy_version: "pub-policy-1",
    snapshot_id: "sample-test",
    decision: "ANSWER",
    intent_id: "INT-01",
    answer_text: "Zoiko iD is designed as identity infrastructure.",
    claims: [],
    citations: [],
    capabilities: [],
    actions: [],
    safety: { reason: "OK", sensitive_input_blocked: false, sample_content: true, ai_assisted: false },
    ...over,
  };
}

export interface Reply {
  status: number;
  body?: unknown;
  headers?: Record<string, string>;
  delay?: Promise<void>;
}
export type Handler = (call: { body: unknown; headers: Record<string, string> }) => Reply | Promise<Reply>;
export interface Call {
  method: string;
  path: string;
  url: string;
  body: unknown;
  headers: Record<string, string>;
  credentials: RequestCredentials | undefined;
}

/** A fake same-origin API. Unhandled routes answer 404 so a test notices a call it did not expect. */
export function mockApi(routes: Record<string, Handler | Reply>) {
  const calls: Call[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const path = url.startsWith(BASE) ? url.slice(BASE.length) : url;
    const headers: Record<string, string> = {};
    for (const [k, v] of Object.entries((init?.headers ?? {}) as Record<string, string>)) headers[k.toLowerCase()] = v;
    const body = typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined;
    calls.push({ method, path, url, body, headers, credentials: init?.credentials });
    const h = routes[`${method} ${path}`];
    if (!h) return new Response(JSON.stringify({ error: { code: "NOT_FOUND", message: "x" } }), { status: 404 });
    const r = typeof h === "function" ? await h({ body, headers }) : h;
    if (r.delay) await r.delay;
    const empty = r.status === 204 || r.body === undefined;
    return new Response(empty ? null : JSON.stringify(r.body), { status: r.status, headers: { "content-type": "application/json", ...r.headers } });
  }) as unknown as typeof fetch & { mock: { calls: unknown[][] } };
  return { fetchImpl, calls };
}

export const err = (status: number, code: string, extra: Record<string, unknown> = {}): Reply => ({
  status,
  body: { error: { code, message: "m", correlation_id: "cor_1", ...extra } },
});

/** The routes every chat test needs. */
export function baseRoutes(extra: Record<string, Handler | Reply> = {}): Record<string, Handler | Reply> {
  return {
    "GET /bootstrap": { status: 200, body: BOOT },
    "POST /sessions": { status: 201, body: { session_expires_at: "2099-01-01T00:00:00Z", idle_seconds: 1800 } },
    "DELETE /sessions": { status: 204 },
    ...extra,
  };
}
