/** Minimal JSON transport over fetch. Every non-2xx becomes a thrown ApiError so query/mutation
 * libraries see real failures; nothing is retried here (mutations must never be replayed blindly). */
import { ApiError } from "./errors";

export type RequestOptions = {
  query?: Record<string, string | number | boolean | undefined | null>;
  body?: unknown;
  /** Required by the cell on grant creation and mandate reservations. */
  idempotencyKey?: string;
  headers?: Record<string, string>;
  signal?: AbortSignal;
};

export type Transport = {
  request<T>(method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE", path: string, opts?: RequestOptions): Promise<T>;
};

export type TransportConfig = {
  /** "/api/bff" for token-bearing calls, "" for same-origin browser flows. */
  baseUrl: string;
  /** CSRF token for state-changing calls through the BFF. */
  csrf?: () => string | undefined;
  fetchImpl?: typeof fetch;
};

export function newCorrelationId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

/** Encodes one path segment while keeping the cell's `{id}:action` syntax readable. */
export function seg(value: string): string {
  return encodeURIComponent(value).replace(/%3A/gi, ":");
}

export function createTransport(config: TransportConfig): Transport {
  const doFetch = config.fetchImpl ?? ((...a: Parameters<typeof fetch>) => fetch(...a));
  return {
    async request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
      const qs = new URLSearchParams();
      for (const [k, v] of Object.entries(opts.query ?? {})) if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
      const url = `${config.baseUrl}${path}${qs.size ? `?${qs.toString()}` : ""}`;
      const headers: Record<string, string> = { accept: "application/json", "x-correlation-id": newCorrelationId(), ...opts.headers };
      if (opts.body !== undefined) headers["content-type"] = "application/json";
      if (opts.idempotencyKey) headers["idempotency-key"] = opts.idempotencyKey;
      if (method !== "GET") {
        const csrf = config.csrf?.();
        if (csrf) headers["x-zid-csrf"] = csrf;
      }
      const res = await doFetch(url, {
        method,
        headers,
        body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
        credentials: "same-origin",
        cache: "no-store",
        signal: opts.signal,
      });
      if (!res.ok) throw await ApiError.from(res);
      if (res.status === 204) return undefined as T;
      const text = await res.text();
      return (text ? JSON.parse(text) : undefined) as T;
    },
  };
}
