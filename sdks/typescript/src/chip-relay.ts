/**
 * The device side of the chip relay (CR-108): carry card commands between a chip and the platform, and nothing else.
 *
 * The platform runs the protocol (Basic Access Control, secure messaging, file reading, Active Authentication) and keeps no state between steps: each reply carries a
 * sealed `token` that the device returns, untouched, with the chip's next answer. The device only needs one thing it must get from the operating system: `transceive`,
 * a function that sends the bytes it is given to the chip and returns the bytes the chip answers. On Android that is `IsoDep.transceive`; on iOS
 * `NFCISO7816Tag.sendCommand`; with a desktop reader, a smart-card transmit. A browser cannot provide it (Web NFC reads NDEF tags only), so in a browser this runs inside
 * an app shell that can.
 *
 * What this module does: open a read, loop (send the command the platform asked for, hand back the answer and the token), and stop when the read ends or anything is
 * refused. If the device fails it simply stops: the token expires on its own. What it never does: look inside a command, an answer or a token, keep any of them, log the
 * document details, or decide anything. No runtime dependencies: `fetch` is injectable and bytes are base64 on the wire.
 */

/** Send a command APDU to the chip and return its answer. Provided by the operating system's NFC layer. Throw if the chip is lost. */
export type Transceive = (apdu: Uint8Array) => Promise<Uint8Array>;

export type ChipRelayFetch = (
  url: string,
  init: { method: string; headers: Record<string, string>; body?: string },
) => Promise<{ status: number; text(): Promise<string> }>;

export type ChipRelayConfig = {
  /** The cell's address, without a trailing slash. */
  baseUrl: string;
  /** Headers for each request (the authorization). Called per request so a short-lived token can be refreshed. */
  headers: () => Record<string, string> | Promise<Record<string, string>>;
  fetch?: ChipRelayFetch;
  /** A ceiling on the commands in one read; the platform has its own. Default 600. */
  maxSteps?: number;
};

/** What the chip's access keys come from, sent once to open the read and never stored or logged here: the three printed details (`document_valid_to` is the document's printed end date, YYMMDD), or only the Card Access Number (`can`) printed on the document, which works for a chip that offers PACE. */
export type ChipDocumentDetails =
  | { document_number: string; date_of_birth: string; document_valid_to: string; can?: undefined; include_photo?: boolean }
  | { can: string; document_number?: undefined; date_of_birth?: undefined; document_valid_to?: undefined; include_photo?: boolean };

/** What the platform holds after a read. `token` is what `finishChipRead` needs. The read has NOT been judged yet. */
export type ChipReadSummary = {
  token: string;
  groups_read: number[];
  groups_missing: string[];
  active_authentication_answered: boolean;
  /** Seconds the token stays valid: how long the person has to finish (for instance to capture a selfie first). */
  expires_in: number;
};

export type FaceCapture = { probe_base64: string; liveness_base64: string };

/** The platform refused a step or the device failed. `code` is the platform's stable code, or `DEVICE_ERROR` / `TOO_MANY_STEPS` from this module. */
export class ChipRelayError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
    message = "",
  ) {
    super(message || code);
    this.name = "ChipRelayError";
  }
}

export function toBase64(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}

export function fromBase64(text: string): Uint8Array {
  const raw = atob(text);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

type Reply = {
  state: "READING" | "READ";
  token: string;
  apdu_base64?: string;
  expires_in?: number;
  groups_read?: number[];
  groups_missing?: string[];
  active_authentication_answered?: boolean;
};

class Relay {
  private readonly base: string;
  constructor(
    private readonly cfg: ChipRelayConfig,
    verificationId: string,
  ) {
    this.base = `${cfg.baseUrl}/v2/self-run/verifications/${encodeURIComponent(verificationId)}/chip-link`;
  }

  async call(path: string, body: unknown): Promise<Record<string, unknown>> {
    const doFetch: ChipRelayFetch = this.cfg.fetch ?? ((url, init) => fetch(url, init) as ReturnType<ChipRelayFetch>);
    const headers = { "content-type": "application/json", accept: "application/json", ...(await this.cfg.headers()) };
    const res = await doFetch(`${this.base}${path}`, { method: "POST", headers, body: JSON.stringify(body ?? {}) });
    const text = await res.text();
    let parsed: Record<string, unknown> = {};
    try {
      parsed = text ? (JSON.parse(text) as Record<string, unknown>) : {};
    } catch {
      parsed = {};
    }
    if (res.status < 200 || res.status >= 300) {
      // the platform's own words only; the request body (which holds the document details and the sealed token) is never echoed into an error
      const code = String(parsed.code ?? parsed.error ?? "CHIP_RELAY_ERROR").toUpperCase();
      throw new ChipRelayError(code, res.status, typeof parsed.detail === "string" ? parsed.detail : typeof parsed.message === "string" ? parsed.message : code);
    }
    return parsed;
  }
}

/**
 * Read a chip through the relay. Resolves with what the platform now holds (which groups were read, whether Active Authentication was answered) and the token to judge
 * it with; the read has NOT been judged yet. Call `finishChipRead` to have it judged. Rejects with `ChipRelayError`. If the device fails the read just stops.
 */
export async function readChipThroughRelay(
  cfg: ChipRelayConfig,
  verificationId: string,
  details: ChipDocumentDetails,
  transceive: Transceive,
): Promise<ChipReadSummary> {
  const relay = new Relay(cfg, verificationId);
  const limit = cfg.maxSteps ?? 600;
  let reply = (await relay.call("", details)) as unknown as Reply;
  for (let step = 0; reply.state === "READING"; step++) {
    if (step >= limit) throw new ChipRelayError("TOO_MANY_STEPS", 0, "the read took more commands than allowed");
    let answer: Uint8Array;
    try {
      answer = await transceive(fromBase64(reply.apdu_base64 ?? ""));
    } catch {
      // the chip was lost or the radio failed: say only that the device failed. Nothing is left running on the platform, and the token expires by itself.
      throw new ChipRelayError("DEVICE_ERROR", 0, "the device could not talk to the chip");
    }
    reply = (await relay.call("/step", { token: reply.token, response_base64: toBase64(answer) })) as unknown as Reply;
  }
  return {
    token: reply.token,
    groups_read: reply.groups_read ?? [],
    groups_missing: reply.groups_missing ?? [],
    active_authentication_answered: reply.active_authentication_answered === true,
    expires_in: reply.expires_in ?? 0,
  };
}

/** Have the platform judge a finished read. A live face may go with it; the platform ignores it unless the deployment has an accepted engine. The session is decided once. */
export async function finishChipRead(
  cfg: ChipRelayConfig,
  verificationId: string,
  token: string,
  face?: FaceCapture,
): Promise<Record<string, unknown>> {
  return new Relay(cfg, verificationId).call("/finish", face ? { token, face } : { token });
}
