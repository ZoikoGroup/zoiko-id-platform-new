/**
 * Second, independent offline verifier for Zoiko evidence bundles (zid-evidence-bundle/1; ZID-CP-WF08 E05, CR-170).
 *
 * Written from the format description, not ported line by line: RFC 8785 canonical JSON, `<alg>:<hex>` digests, RFC 9162
 * inclusion proofs, the ledger entry hash, and the checkpoint JWS (ES256 or Ed25519). No runtime dependencies: only WebCrypto.
 * Like the Python verifier it never throws on bad input (every failure is a failed check) and it never trusts keys that the
 * bundle itself brought unless the caller gave none, which it reports as `keysSource: "bundle"`.
 *
 * It says what is intact and who signed it. It does not say that a decision was right.
 */

export const BUNDLE_FORMAT = "zid-evidence-bundle/1";
export const CHECKPOINT_TYP = "zid-evidence-checkpoint+jws";

export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
export type Check = { name: string; ok: boolean; detail: string };
export type Report = { ok: boolean; structuralOnly: boolean; keysSource: "trusted" | "bundle"; checks: Check[] };
export type Jwk = Record<string, string>;

const SIZES: Record<string, number> = { "sha-256": 32, "sha-384": 48, "sha-512": 64 };
const SUBTLE: Record<string, string> = { "sha-256": "SHA-256", "sha-384": "SHA-384", "sha-512": "SHA-512" };
const enc = new TextEncoder();

// ---------------------------------------------------------------------------------------------- canonical JSON (RFC 8785)

function jcsString(s: string): string {
  let out = '"';
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) {
      const d = s.charCodeAt(i + 1);
      if (!(d >= 0xdc00 && d <= 0xdfff)) throw new Error("lone surrogate in string");
      out += s[i]! + s[i + 1]!;
      i++;
      continue;
    }
    if (c >= 0xdc00 && c <= 0xdfff) throw new Error("lone surrogate in string");
    if (c === 8) out += "\\b";
    else if (c === 9) out += "\\t";
    else if (c === 10) out += "\\n";
    else if (c === 12) out += "\\f";
    else if (c === 13) out += "\\r";
    else if (c === 0x22) out += '\\"';
    else if (c === 0x5c) out += "\\\\";
    else if (c < 0x20) out += `\\u${c.toString(16).padStart(4, "0")}`;
    else out += s[i]!;
  }
  return out + '"';
}

/** JSON.stringify of a number already follows ECMAScript Number::toString, which is what RFC 8785 requires. */
function jcsNumber(n: number): string {
  if (!Number.isFinite(n)) throw new Error("non-finite number");
  // Known difference from the Python verifier: Python refuses an INTEGER beyond 2^53 but accepts the float 1e21. After JSON.parse
  // the two are the same number here, so this side cannot tell them apart; it reads both as the float, which is what RFC 8785 says.
  return Object.is(n, -0) ? "0" : JSON.stringify(n);
}

function ser(v: unknown): string {
  if (v === null) return "null";
  if (v === true) return "true";
  if (v === false) return "false";
  if (typeof v === "number") return jcsNumber(v);
  if (typeof v === "string") return jcsString(v);
  if (Array.isArray(v)) return `[${v.map(ser).join(",")}]`;
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    // UTF-16 code unit order is what the default string comparison of JS does.
    const keys = Object.keys(o).sort();
    return `{${keys.map((k) => `${jcsString(k)}:${ser(o[k])}`).join(",")}}`;
  }
  throw new Error(`not a JSON value: ${typeof v}`);
}

export const canonicalize = (v: unknown): Uint8Array => enc.encode(ser(v));

// ---------------------------------------------------------------------------------------------------------------- digests

const hex = (b: Uint8Array): string => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
function unhex(s: string): Uint8Array {
  if (s.length % 2 || !/^[0-9a-f]*$/.test(s)) throw new Error("digest value is not lowercase hex");
  return Uint8Array.from(s.match(/../g) ?? [], (h) => parseInt(h, 16));
}

type Digest = { alg: string; value: Uint8Array };
function parseDigest(text: unknown): Digest {
  if (typeof text !== "string" || !text.includes(":")) throw new Error("digest must be '<alg>:<hex>'");
  const i = text.indexOf(":");
  const alg = text.slice(0, i);
  if (!(alg in SIZES)) throw new Error(`hash algorithm not allowed: ${alg}`);
  const value = unhex(text.slice(i + 1));
  if (value.length !== SIZES[alg]) throw new Error(`${alg} digest has the wrong length`);
  return { alg, value };
}
const fmt = (d: Digest): string => `${d.alg}:${hex(d.value)}`;

async function h(alg: string, data: Uint8Array): Promise<Uint8Array> {
  const name = SUBTLE[alg];
  if (!name) throw new Error(`hash algorithm not allowed: ${alg}`);
  return new Uint8Array(await crypto.subtle.digest(name, data as BufferSource));
}
const cat = (...p: Uint8Array[]): Uint8Array => {
  const out = new Uint8Array(p.reduce((n, x) => n + x.length, 0));
  let o = 0;
  for (const x of p) {
    out.set(x, o);
    o += x.length;
  }
  return out;
};
const same = (a: Uint8Array, b: Uint8Array): boolean => a.length === b.length && a.every((x, i) => x === b[i]);
const hashJson = async (v: unknown, alg: string): Promise<Digest> => ({ alg, value: await h(alg, canonicalize(v)) });
const digestEq = (a: Digest, b: Digest): boolean => a.alg === b.alg && same(a.value, b.value);

// ------------------------------------------------------------------------------------------------- Merkle (RFC 9162 2.1.3.2)

async function verifyInclusion(leafIndex: number, treeSize: number, leaf: Uint8Array, proof: Uint8Array[], root: Uint8Array, alg: string): Promise<boolean> {
  if (!(leafIndex >= 0 && leafIndex < treeSize)) return false;
  let fn = leafIndex;
  let sn = treeSize - 1;
  let r = leaf;
  for (const p of proof) {
    if (sn === 0) return false;
    if (fn & 1 || fn === sn) {
      r = await h(alg, cat(Uint8Array.of(1), p, r));
      while (!(fn & 1) && fn !== 0) {
        fn = Math.floor(fn / 2);
        sn = Math.floor(sn / 2);
      }
    } else {
      r = await h(alg, cat(Uint8Array.of(1), r, p));
    }
    fn = Math.floor(fn / 2);
    sn = Math.floor(sn / 2);
  }
  return sn === 0 && same(r, root);
}

// ------------------------------------------------------------------------------------------------------------------- JWS

function b64d(text: string): Uint8Array {
  if (!/^[A-Za-z0-9_-]*$/.test(text)) throw new Error("malformed base64url");
  const s = text.replaceAll("-", "+").replaceAll("_", "/") + "=".repeat((4 - (text.length % 4)) % 4);
  const bin = atob(s);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}
const parseObj = (b: Uint8Array): Record<string, unknown> => {
  const v: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(b));
  if (v === null || typeof v !== "object" || Array.isArray(v)) throw new Error("JWS header and payload must be JSON objects");
  return v as Record<string, unknown>;
};

async function verifyCompact(token: string, keys: readonly Jwk[], typ: string): Promise<{ header: Record<string, unknown>; payload: Record<string, unknown> }> {
  const parts = typeof token === "string" ? token.split(".") : [];
  if (parts.length !== 3) throw new Error("malformed compact JWS");
  const header = parseObj(b64d(parts[0]!));
  const payload = parseObj(b64d(parts[1]!));
  const alg = header["alg"];
  if (alg !== "ES256" && alg !== "Ed25519" && alg !== "EdDSA") throw new Error(`algorithm not allowed: ${String(alg)}`);
  if (header["typ"] !== typ) throw new Error(`expected typ ${typ}`);
  if (["jku", "x5u", "x5c", "jwk"].some((k) => k in header)) throw new Error("remote or embedded keys are never trusted");
  const found = keys.filter((k) => k["kid"] === header["kid"]);
  if (found.length !== 1) throw new Error(`unknown signing key ${String(header["kid"])}`);
  const jwk = found[0]!;
  if (["d", "p", "q", "k"].some((k) => k in jwk)) throw new Error("a verification key must not carry private members");
  if (jwk["alg"] !== undefined && jwk["alg"] !== alg) throw new Error("key algorithm does not match the header");
  const sig = b64d(parts[2]!);
  const input = enc.encode(`${parts[0]}.${parts[1]}`);
  let ok = false;
  try {
    if (alg === "ES256") {
      if (jwk["kty"] !== "EC" || jwk["crv"] !== "P-256" || sig.length !== 64) throw new Error("bad key");
      const key = await crypto.subtle.importKey("jwk", { kty: "EC", crv: "P-256", x: jwk["x"], y: jwk["y"] }, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
      ok = await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, sig as BufferSource, input as BufferSource);
    } else {
      if (jwk["kty"] !== "OKP" || jwk["crv"] !== "Ed25519") throw new Error("bad key");
      const key = await crypto.subtle.importKey("jwk", { kty: "OKP", crv: "Ed25519", x: jwk["x"] }, { name: "Ed25519" }, false, ["verify"]);
      ok = await crypto.subtle.verify({ name: "Ed25519" }, key, sig as BufferSource, input as BufferSource);
    }
  } catch {
    ok = false;
  }
  if (!ok) throw new Error("signature does not verify");
  return { header, payload };
}

// --------------------------------------------------------------------------------------------------------------- the bundle

const ENTRY_FIELDS = ["partition_id", "sequence_no", "record_id", "record_type", "record_hash", "previous_entry_hash", "committed_at", "effective_at", "observed_at", "evidence_class"] as const;
const MANIFEST_FIELDS = ["checkpoint_id", "partition_id", "first_sequence", "last_sequence", "tree_size", "merkle_root", "previous_checkpoint_hash", "created_at", "signer_key_id", "alg"] as const;
type Obj = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

export async function verifyBundle(bundle: unknown, opts: { trustedKeys?: readonly Jwk[]; requireTimestamp?: boolean } = {}): Promise<Report> {
  const checks: Check[] = [];
  const add = (name: string, ok: boolean, detail = ""): boolean => {
    checks.push({ name, ok, detail });
    return ok;
  };
  const report = (): Report => ({
    ok: checks.length > 0 && checks.every((c) => c.ok),
    structuralOnly: checks.some((c) => c.name === "record_hash" && c.detail === "record not included"),
    keysSource: opts.trustedKeys ? "trusted" : "bundle",
    checks,
  });
  try {
    const b = bundle as Obj;
    if (!add("format", b?.format === BUNDLE_FORMAT, String(b?.format))) return report();
    const p = b.profile as Obj;
    const alg = p.hash_alg as string;
    if (!add("profile", alg in SIZES && p.canonicalization === "RFC8785-JCS" && p.merkle === "RFC9162" && p.signature === "JWS", String(alg))) return report();

    const raw: Obj = { ...b.ledger_entry };
    const stored = parseDigest(raw.entry_hash);
    delete raw.entry_hash;
    const entryObj: Record<string, Json> = {};
    for (const f of ENTRY_FIELDS) entryObj[f] = raw[f] === undefined ? null : raw[f];
    if (typeof raw.sequence_no !== "number" || raw.sequence_no < 1 || !Number.isInteger(raw.sequence_no)) throw new Error("sequence numbers start at 1");
    if ((raw.sequence_no === 1) !== (raw.previous_entry_hash === null || raw.previous_entry_hash === undefined)) throw new Error("only the first entry has no predecessor");
    if (!["E0", "E1", "E2", "E3"].includes(raw.evidence_class)) throw new Error("unknown evidence class");
    const recordHash = parseDigest(raw.record_hash);
    if (raw.previous_entry_hash != null) parseDigest(raw.previous_entry_hash);
    add("record_identity", raw.record_id === b.record_id && raw.record_type === b.record_type);

    if (b.record === null || b.record === undefined) add("record_hash", true, "record not included");
    else {
      try {
        const ok = digestEq(await hashJson(b.record, recordHash.alg), recordHash);
        add("record_hash", ok, ok ? "" : "record digest differs from the ledger");
      } catch (e) {
        add("record_hash", false, String((e as Error).message));
      }
    }

    const entryOk = digestEq(await hashJson(entryObj, stored.alg), stored);
    add("entry_hash", entryOk, entryOk ? "" : "ledger entry fields do not match entry_hash");

    const cp: Obj = { ...b.checkpoint };
    const signature = cp.signature as string;
    delete cp.signature;
    const tsRef = cp.external_timestamp_ref ?? null;
    delete cp.external_timestamp_ref;
    const manifest: Record<string, Json> = {};
    for (const f of MANIFEST_FIELDS) manifest[f] = cp[f] === undefined ? null : cp[f];
    const first = cp.first_sequence as number;
    const last = cp.last_sequence as number;
    if (cp.tree_size !== last - first + 1) throw new Error("tree_size does not match the sequence range");
    const root = parseDigest(cp.merkle_root);
    if (cp.previous_checkpoint_hash != null) parseDigest(cp.previous_checkpoint_hash);
    const inRange = cp.partition_id === raw.partition_id && first <= raw.sequence_no && raw.sequence_no <= last;
    add("checkpoint_covers_entry", inRange);

    const proof = b.inclusion_proof as Obj;
    const hashes = (proof.hashes as unknown[]).map(parseDigest);
    const leaf = await h(root.alg, cat(Uint8Array.of(0), enc.encode(fmt(stored))));
    const included =
      inRange &&
      hashes.every((d) => d.alg === root.alg) &&
      proof.leaf_index === raw.sequence_no - first &&
      proof.tree_size === last - first + 1 &&
      (await verifyInclusion(proof.leaf_index, last - first + 1, leaf, hashes.map((d) => d.value), root.value, root.alg));
    add("inclusion_proof", included, included ? "" : "entry not in checkpoint root");

    const keys = (opts.trustedKeys ?? b.keys?.keys ?? []) as Jwk[];
    try {
      const { header, payload } = await verifyCompact(signature, keys, CHECKPOINT_TYP);
      if (header["kid"] !== cp.signer_key_id || header["alg"] !== cp.alg) throw new Error("signature key id or algorithm differs from the manifest");
      if (!same(canonicalize(payload), canonicalize(manifest))) throw new Error("signed payload differs from the manifest");
      add("checkpoint_signature", true, String(cp.signer_key_id));
    } catch (e) {
      add("checkpoint_signature", false, String((e as Error).message));
    }
    // An RFC 3161 timestamp is not parsed by this verifier: a present one is reported as not verified, never as proof of time.
    if (tsRef === null) {
      if (opts.requireTimestamp) add("external_timestamp", false, "checkpoint is not anchored");
    } else add("external_timestamp", !opts.requireTimestamp, "present, not verified (this verifier does not read RFC 3161 tokens)");
  } catch (e) {
    add("structure", false, `${(e as Error).name}: ${(e as Error).message}`);
  }
  return report();
}
