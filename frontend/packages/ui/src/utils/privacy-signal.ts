/** Global Privacy Control (ZID-PRIV-001 PRIV-21): when the browser says "do not sell or share" the signed-in
 * subject's opt-out is recorded, with no form and no identity proof.
 *
 * The browser sends `Sec-GPC: 1` with every request it makes to the app; the BFF forwards it to the cell, which is
 * the only place that decides (it records the opt-out only on the exact signal, and repeating it changes nothing).
 * This helper decides *when to ask*: only if the browser advertises the signal, and once per page load. The
 * "already sent" state is held in memory, not in web storage (no storage is used for anything here). */
export type PrivacySignalEnv = {
  /** `navigator.globalPrivacyControl` */
  gpc: boolean | undefined;
  /** In-memory state shared by the calls of one page load. */
  memo: { sent: boolean };
  send: () => Promise<unknown>;
};

/** True when a signal was sent now. A failed send is not remembered (it is tried again later) and never thrown. */
export async function sendGlobalPrivacySignal(env: PrivacySignalEnv): Promise<boolean> {
  if (env.gpc !== true || env.memo.sent) return false;
  try {
    await env.send();
  } catch {
    return false;
  }
  env.memo.sent = true;
  return true;
}
