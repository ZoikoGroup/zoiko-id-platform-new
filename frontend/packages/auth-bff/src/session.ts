/** Server-side console sessions (DEC-H1). The browser holds only an opaque, httpOnly cookie id;
 * access/refresh tokens live here and never reach browser JavaScript (ZID-UX-001 §29). */
import { randomBytes } from "node:crypto";

export type ConsoleSession = {
  id: string;
  /** Anti-CSRF token, echoed by the browser in `x-zid-csrf` on every state-changing call. */
  csrf: string;
  accessToken: string;
  refreshToken?: string;
  idToken?: string;
  /** Epoch seconds. */
  accessTokenExpiresAt: number;
  createdAt: number;
  /** The OAuth client (workspace) this session signed in through; refresh and logout use it. */
  clientId?: string;
  /** Display-only claims from the id_token; never used for authorization. */
  subject: { sub: string; tenantId?: string; acr?: string; authTime?: number };
};

export type PendingLogin = {
  state: string;
  nonce: string;
  codeVerifier: string;
  returnTo: string;
  createdAt: number;
  clientId?: string;
};

/** Valkey in deployed cells (cell-local, DEC-H1); in-memory for local development and tests. */
export interface SessionStore {
  get(id: string): Promise<ConsoleSession | null>;
  set(session: ConsoleSession, ttlSeconds: number): Promise<void>;
  delete(id: string): Promise<void>;
  putPending(p: PendingLogin, ttlSeconds: number): Promise<void>;
  takePending(state: string): Promise<PendingLogin | null>;
}

export function randomToken(bytes = 32): string {
  return randomBytes(bytes).toString("base64url");
}

type Entry<T> = { value: T; expiresAt: number };

export class MemorySessionStore implements SessionStore {
  private sessions = new Map<string, Entry<ConsoleSession>>();
  private pending = new Map<string, Entry<PendingLogin>>();

  private live<T>(map: Map<string, Entry<T>>, key: string): T | null {
    const e = map.get(key);
    if (!e) return null;
    if (e.expiresAt < Date.now()) {
      map.delete(key);
      return null;
    }
    return e.value;
  }

  async get(id: string) {
    return this.live(this.sessions, id);
  }
  async set(session: ConsoleSession, ttlSeconds: number) {
    this.sessions.set(session.id, { value: session, expiresAt: Date.now() + ttlSeconds * 1000 });
  }
  async delete(id: string) {
    this.sessions.delete(id);
  }
  async putPending(p: PendingLogin, ttlSeconds: number) {
    this.pending.set(p.state, { value: p, expiresAt: Date.now() + ttlSeconds * 1000 });
  }
  async takePending(state: string) {
    const v = this.live(this.pending, state);
    this.pending.delete(state);
    return v;
  }
}

/** Minimal command surface of a Valkey/Redis client (iovalkey, ioredis, node-redis adapters). */
export type KeyValueClient = {
  get(key: string): Promise<string | null>;
  set(key: string, value: string, mode: "EX", seconds: number): Promise<unknown>;
  del(key: string): Promise<unknown>;
  getdel?(key: string): Promise<string | null>;
};

export class ValkeySessionStore implements SessionStore {
  constructor(
    private readonly client: KeyValueClient,
    private readonly prefix: string,
  ) {}

  async get(id: string) {
    const raw = await this.client.get(`${this.prefix}:s:${id}`);
    return raw ? (JSON.parse(raw) as ConsoleSession) : null;
  }
  async set(session: ConsoleSession, ttlSeconds: number) {
    await this.client.set(`${this.prefix}:s:${session.id}`, JSON.stringify(session), "EX", ttlSeconds);
  }
  async delete(id: string) {
    await this.client.del(`${this.prefix}:s:${id}`);
  }
  async putPending(p: PendingLogin, ttlSeconds: number) {
    await this.client.set(`${this.prefix}:p:${p.state}`, JSON.stringify(p), "EX", ttlSeconds);
  }
  async takePending(state: string) {
    const key = `${this.prefix}:p:${state}`;
    const raw = this.client.getdel ? await this.client.getdel(key) : await this.client.get(key);
    if (!this.client.getdel) await this.client.del(key);
    return raw ? (JSON.parse(raw) as PendingLogin) : null;
  }
}
