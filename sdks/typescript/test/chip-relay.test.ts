import { describe, expect, it } from "vitest";

import { ChipRelayError, finishChipRead, fromBase64, readChipThroughRelay, toBase64, transceiveFromNative, type ChipRelayFetch } from "../src/chip-relay.js";

const CFG_BASE = "https://cell.test";
const DETAILS = { document_number: "L898902C3", date_of_birth: "740812", document_valid_to: "320415" };
const bytes = (...n: number[]) => Uint8Array.from(n);

type Call = { url: string; body: Record<string, unknown> };

/** A scripted platform: it asks for the commands in `commands` one at a time (each reply carries a fresh token), then reports the read. */
function platform(commands: Uint8Array[], opts: { failAt?: { call: number; status: number; code: string } } = {}) {
  const calls: Call[] = [];
  const fetchImpl: ChipRelayFetch = async (url, init) => {
    const body = init.body ? (JSON.parse(init.body) as Record<string, unknown>) : {};
    calls.push({ url, body });
    const n = calls.length;
    if (opts.failAt && n === opts.failAt.call) {
      return { status: opts.failAt.status, text: async () => JSON.stringify({ code: opts.failAt!.code, detail: "refused" }) };
    }
    if (url.endsWith("/finish")) return { status: 200, text: async () => JSON.stringify({ status: "VERIFIED" }) };
    const answered = calls.filter((c) => c.url.endsWith("/step")).length;
    if (answered < commands.length) {
      return { status: 201, text: async () => JSON.stringify({ state: "READING", token: `T${answered}`, apdu_base64: toBase64(commands[answered]!) }) };
    }
    return {
      status: 200,
      text: async () =>
        JSON.stringify({ state: "READ", token: "TDONE", expires_in: 300, groups_read: [1, 15], groups_missing: ["DG14"], active_authentication_answered: true }),
    };
  };
  return { calls, fetchImpl };
}

const cfg = (fetchImpl: ChipRelayFetch, extra: object = {}) => ({ baseUrl: CFG_BASE, headers: () => ({ authorization: "Bearer t" }), fetch: fetchImpl, ...extra });
const BASE = `${CFG_BASE}/v2/self-run/verifications/V1/chip-link`;

describe("the device side of the chip relay", () => {
  it("sends each command to the chip, hands back each answer with the token that came with the command, and reports what was read", async () => {
    const { calls, fetchImpl } = platform([bytes(1, 2), bytes(3), bytes(4, 5, 6)]);
    const seen: number[][] = [];
    const out = await readChipThroughRelay(cfg(fetchImpl), "V1", DETAILS, async (apdu) => {
      seen.push([...apdu]);
      return bytes(0x90, 0x00, apdu.length);
    });
    expect(seen).toEqual([[1, 2], [3], [4, 5, 6]]);
    expect(calls.map((c) => c.url.replace(BASE, ""))).toEqual(["", "/step", "/step", "/step"]);
    const steps = calls.filter((c) => c.url.endsWith("/step"));
    expect(steps.map((c) => c.body.token)).toEqual(["T0", "T1", "T2"]); // each answer returns the token its command came with
    expect(steps.map((c) => [...fromBase64(String(c.body.response_base64))])).toEqual([[0x90, 0x00, 2], [0x90, 0x00, 1], [0x90, 0x00, 3]]);
    expect(out).toEqual({ token: "TDONE", groups_read: [1, 15], groups_missing: ["DG14"], active_authentication_answered: true, expires_in: 300 });
  });

  it("opens the read with the document details and sends them nowhere else", async () => {
    const { calls, fetchImpl } = platform([bytes(1)]);
    await readChipThroughRelay(cfg(fetchImpl), "V1", DETAILS, async () => bytes(0x90, 0x00));
    expect(calls[0]!.body).toEqual(DETAILS);
    for (const c of calls.slice(1)) expect(JSON.stringify(c.body)).not.toContain("L898902C3");
  });

  it("stops and says only that the device failed when the chip is lost, with nothing to clean up", async () => {
    const { calls, fetchImpl } = platform([bytes(1), bytes(2)]);
    let n = 0;
    const err = await readChipThroughRelay(cfg(fetchImpl), "V1", DETAILS, async () => {
      if (++n === 2) throw new Error("tag lost: L898902C3");
      return bytes(0x90, 0x00);
    }).catch((e) => e);
    expect(err).toBeInstanceOf(ChipRelayError);
    expect(err.code).toBe("DEVICE_ERROR");
    expect(String(err.message)).not.toContain("L898902C3");
    expect(calls.length).toBe(2); // it did not call back: the platform keeps nothing waiting
  });

  it("stops at a ceiling on the number of commands", async () => {
    const forever = Array.from({ length: 50 }, () => bytes(1));
    const { fetchImpl } = platform(forever);
    const err = await readChipThroughRelay(cfg(fetchImpl, { maxSteps: 5 }), "V1", DETAILS, async () => bytes(0x90, 0x00)).catch((e) => e);
    expect(err.code).toBe("TOO_MANY_STEPS");
  });

  it("turns a refusal into the platform's own code and never echoes the request or the token", async () => {
    const { fetchImpl } = platform([bytes(1), bytes(2)], { failAt: { call: 3, status: 422, code: "bac_not_accepted" } });
    const err = await readChipThroughRelay(cfg(fetchImpl), "V1", DETAILS, async () => bytes(0x69, 0x82)).catch((e) => e);
    expect(err).toBeInstanceOf(ChipRelayError);
    expect(err.code).toBe("BAC_NOT_ACCEPTED");
    expect(err.status).toBe(422);
    expect(String(err.message)).not.toContain("L898902C3");
    expect(String(err.message)).not.toContain("T0");
  });

  it("asks for the headers on every request so a short-lived token can be refreshed", async () => {
    const { fetchImpl } = platform([bytes(1), bytes(2)]);
    let tokens = 0;
    await readChipThroughRelay({ baseUrl: CFG_BASE, fetch: fetchImpl, headers: () => ({ authorization: `Bearer ${++tokens}` }) }, "V1", DETAILS, async () => bytes(0x90, 0x00));
    expect(tokens).toBe(3); // one request to open the read and one for each of the two answers
  });

  it("finishes a read with the token, with or without a face", async () => {
    const { calls, fetchImpl } = platform([]);
    const plain = await finishChipRead(cfg(fetchImpl), "V1", "TDONE");
    const withFace = await finishChipRead(cfg(fetchImpl), "V1", "TDONE", { probe_base64: "AA==", liveness_base64: "AA==" });
    expect(plain.status).toBe("VERIFIED");
    expect(calls[0]!.body).toEqual({ token: "TDONE" });
    expect(calls[1]!.body).toEqual({ token: "TDONE", face: { probe_base64: "AA==", liveness_base64: "AA==" } });
    expect(calls.every((c) => c.url === `${BASE}/finish`)).toBe(true);
    expect(withFace.status).toBe("VERIFIED");
  });

  it("round-trips bytes through base64", () => {
    const all = Uint8Array.from({ length: 256 }, (_, i) => i);
    expect([...fromBase64(toBase64(all))]).toEqual([...all]);
  });
});


describe("transceiveFromNative", () => {
  it("passes the command as base64 and returns the answer with its status bytes", async () => {
    const seen: string[] = [];
    const t = transceiveFromNative({ transceive: (b64) => (seen.push(b64), toBase64(bytes(0xaa, 0x90, 0x00))) });
    expect([...(await t(bytes(1, 2, 3)))]).toEqual([0xaa, 0x90, 0x00]);
    expect(seen).toEqual([toBase64(bytes(1, 2, 3))]);
  });
  it("takes an asynchronous host too", async () => {
    const t = transceiveFromNative({ transceive: async () => toBase64(bytes(0x90, 0x00)) });
    expect([...(await t(bytes(9)))]).toEqual([0x90, 0x00]);
  });
  it("turns every host failure into a device error and says nothing about the cause", async () => {
    for (const host of [
      { transceive: () => "ERROR:tag lost" },
      { transceive: () => "" },
      { transceive: () => "not base64!" },
      { transceive: () => toBase64(bytes(0x90)) }, // shorter than the two status bytes
      { transceive: () => { throw new Error("boom"); } },
      { transceive: async () => { throw new Error("boom"); } },
    ]) {
      const e = await transceiveFromNative(host)(bytes(1)).catch((x: unknown) => x);
      expect(e).toBeInstanceOf(ChipRelayError);
      expect((e as ChipRelayError).code).toBe("DEVICE_ERROR");
      expect(String((e as ChipRelayError).message)).not.toMatch(/tag lost|boom/);
    }
  });
});
