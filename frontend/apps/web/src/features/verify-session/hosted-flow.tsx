"use client";

import { Banner, Button, Card, Checkbox } from "@zoiko/ui";
import { useCallback, useEffect, useRef, useState } from "react";

import { DocumentCapture } from "@/features/account/idv/document-capture";
import { LiveCapture } from "@/features/account/idv/live-capture";
import { fileToBase64 } from "@/features/account/idv/logic";

import { api, declaredComplete, FlowError, launchToken, returnHref, screenFor, step, type Context, type Credential, type Declared, type Method, type Status } from "./flow";
import { dirOf, languageName, messages, offered, type Messages } from "./messages";
import { QrCode } from "./qr";

const POLL_MS = 5000;

/** The person's hosted verification (ZID-CP-WF01 S01 to S12). Every screen is chosen from the server's state; nothing here decides or shows a result. */
export function HostedFlow() {
  const [cred, setCred] = useState<Credential | null>(null);
  const [ctx, setCtx] = useState<Context | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<FlowError | null>(null);
  const [busy, setBusy] = useState(false);
  const [lang, setLang] = useState("en");
  const heading = useRef<HTMLHeadingElement>(null);
  const device = useRef({ nfc: false, camera: true });
  const t = messages(lang);

  // Spend the link once and drop it from the address bar; the session credential lives in memory only (never web storage, §29).
  useEffect(() => {
    device.current = { nfc: "NDEFReader" in window, camera: Boolean(navigator.mediaDevices?.getUserMedia) };
    const token = launchToken(window.location.hash);
    if (token) window.history.replaceState(null, "", window.location.pathname);
    (async () => {
      try {
        if (!token) throw new FlowError("TOKEN_INVALID", messages("en").linkUnusable + ".", 401);
        const got = await api.launch(token);
        const c: Credential = { ref: got.session_ref, token: got.access_token, expires_at: got.expires_at };
        const context = await api.context(c, device.current);
        setCred(c);
        setCtx(context);
        setStatus(context.status);
        setLang(offered(context.languages)[0] ?? "en");
      } catch (e) {
        setError(e instanceof FlowError ? e : new FlowError("ERROR", messages("en").somethingWrong, 0));
      }
    })();
  }, []);

  const screen = status ? screenFor(status) : null;
  useEffect(() => heading.current?.focus(), [screen]);

  // While a person reviews it, check back at a steady pace; the person may also simply leave (S08).
  useEffect(() => {
    if (!cred || screen !== "processing") return;
    const id = setInterval(() => api.status(cred).then(setStatus).catch(() => undefined), POLL_MS);
    return () => clearInterval(id);
  }, [cred, screen]);

  const act = useCallback(
    async (fn: (c: Credential) => Promise<Status>) => {
      if (!cred) return;
      setBusy(true);
      setError(null);
      try {
        setStatus(await fn(cred));
      } catch (e) {
        setError(e instanceof FlowError ? e : new FlowError("ERROR", t.somethingWrong, 0));
      } finally {
        setBusy(false);
      }
    },
    [cred, t],
  );

  if (!status || !ctx || !cred) {
    return error ? <Ended title={t.linkUnusable} body={t.linkUnusableBody(error.message)} /> : <p role="status">{t.opening}</p>;
  }
  const s = step(screen!);
  const name = ctx.requester.name;
  return (
    <section aria-labelledby="flow-h" lang={lang} dir={dirOf(lang)} className="space-y-5">
      <p className="text-sm text-ink-muted">
        {name ?? t.aBusiness} · {t.securedBy(ctx.requester.verified_by)}
        {s ? ` · ${t.stepOf(s.at, s.of)}` : ""}
        {status.mode === "sandbox" ? ` · ${t.testMode}` : ""}
      </p>
      {status.action ? (
        <Banner tone={status.action.category === "USER_CORRECTABLE" ? "warn" : "info"} title={t.whatToDo}>
          {t.action[status.action.code] ?? status.action.message}
        </Banner>
      ) : null}
      {error ? (
        <Banner tone="warn" title={t.stepFailed}>
          {t.action[error.code] ?? error.message}
        </Banner>
      ) : null}
      {screen === "welcome" ? (
        <Welcome
          ctx={ctx}
          t={t}
          lang={lang}
          onLang={setLang}
          heading={heading}
          busy={busy}
          onContinue={(consent, face) => act((c) => api.notices(c, lang, consent, face))}
        />
      ) : null}
      {screen === "choose" ? (
        <Choose
          methods={ctx.methods.filter((m) => !m.biometric || status.biometric_consent)}
          withheld={ctx.methods_withheld}
          t={t}
          heading={heading}
          busy={busy}
          cred={cred}
          onChoose={(m) => act((c) => api.select(c, m.method, device.current))}
        />
      ) : null}
      {screen === "capture" ? <Capture ctx={ctx} t={t} status={status} heading={heading} busy={busy} cred={cred} onSent={setStatus} onError={setError} /> : null}
      {screen === "review-submit" ? (
        <Card>
          <h1 ref={heading} tabIndex={-1} id="flow-h" className="text-xl font-bold text-ink">
            {t.submitTitle}
          </h1>
          <p className="mt-2 text-ink-muted">{t.submitBody}</p>
          <div className="mt-4">
            <Button isDisabled={busy} onPress={() => act(api.submit)}>
              {t.submit}
            </Button>
          </div>
        </Card>
      ) : null}
      {screen === "processing" ? (
        <Card>
          <h1 ref={heading} tabIndex={-1} id="flow-h" className="text-xl font-bold text-ink">
            {t.processingTitle}
          </h1>
          <p role="status" className="mt-2 text-ink-muted">
            {t.processingBody}
          </p>
          <p className="mt-3 text-sm">{t.reference(status.reference)}</p>
        </Card>
      ) : null}
      {screen === "finished" ? (
        <Card>
          <h1 ref={heading} tabIndex={-1} id="flow-h" className="text-xl font-bold text-ink">
            {t.outcome[status.state as keyof Messages["outcome"]].title}
          </h1>
          <p role="status" className="mt-2 text-ink-muted">
            {t.outcome[status.state as keyof Messages["outcome"]].body}
          </p>
          <p className="mt-3 text-sm">{t.reference(status.reference)}</p>
          {returnHref(status) ? (
            <a className="mt-4 inline-block font-semibold text-brand-600 underline" href={returnHref(status)!}>
              {t.returnTo(name)}
            </a>
          ) : null}
        </Card>
      ) : null}
      {screen === "ended" ? <Ended title={status.state === "EXPIRED" ? t.expiredTitle : t.endedTitle} body={t.endedBody(name)} /> : null}
      {screen && !["finished", "ended", "processing"].includes(screen) ? (
        <Button variant="secondary" isDisabled={busy} onPress={() => act(api.cancel)}>
          {t.leave}
        </Button>
      ) : null}
    </section>
  );
}

function Ended({ title, body }: { title: string; body: string }) {
  return (
    <section className="space-y-3">
      <h1 className="text-2xl font-bold tracking-tight text-ink">{title}</h1>
      <p role="status" className="text-ink-muted">
        {body}
      </p>
    </section>
  );
}

type HeadingRef = React.RefObject<HTMLHeadingElement | null>;

/** S01: who is asking, why, the notice, the language, and separate unticked choices: the purpose (only where consent is the basis) and a face comparison. */
function Welcome({
  ctx,
  t,
  lang,
  onLang,
  heading,
  busy,
  onContinue,
}: {
  ctx: Context;
  t: Messages;
  lang: string;
  onLang: (l: string) => void;
  heading: HeadingRef;
  busy: boolean;
  onContinue: (consent: boolean, face: boolean) => void;
}) {
  const [agree, setAgree] = useState(false);
  const [face, setFace] = useState(false);
  const biometric = ctx.methods.some((m) => m.biometric);
  const byConsent = ctx.lawful_basis === "CONSENT";
  return (
    <Card>
      <h1 ref={heading} tabIndex={-1} id="flow-h" className="text-xl font-bold text-ink">
        {t.welcomeTitle}
      </h1>
      <dl className="mt-3 space-y-2 text-sm">
        <div>
          <dt className="font-medium">{t.requestedBy}</dt>
          <dd>{ctx.requester.name ?? "-"}</dd>
        </div>
        <div>
          <dt className="font-medium">{t.purpose}</dt>
          <dd>{ctx.purpose_code.replaceAll("_", " ").toLowerCase()}</dd>
        </div>
        <div>
          <dt className="font-medium">{t.whatWeAsk}</dt>
          <dd>{biometric ? t.askDocumentAndFace : t.askDocument}</dd>
        </div>
        {ctx.notice?.content_uri ? (
          <div>
            <dt className="font-medium">{t.howUsed}</dt>
            <dd>
              <a className="text-brand-600 underline" href={ctx.notice.content_uri} target="_blank" rel="noreferrer">
                {t.readNotice(ctx.notice.notice_version)}
              </a>
            </dd>
          </div>
        ) : null}
      </dl>
      <label className="mt-4 block text-sm font-medium" htmlFor="flow-lang">
        {t.language}
      </label>
      <select id="flow-lang" className="mt-1 rounded border border-border px-2 py-1" value={lang} onChange={(e) => onLang(e.target.value)}>
        {offered(ctx.languages).map((l) => (
          <option key={l} value={l} lang={l}>
            {languageName(l)}
          </option>
        ))}
      </select>
      {byConsent ? (
        <div className="mt-4">
          <Checkbox isSelected={agree} onChange={setAgree}>
            {t.agreePurpose}
          </Checkbox>
        </div>
      ) : null}
      {biometric ? (
        <div className="mt-4">
          <Checkbox isSelected={face} onChange={setFace}>
            {t.agreeFace}
          </Checkbox>
        </div>
      ) : null}
      <div className="mt-4">
        <Button isDisabled={busy || (byConsent && !agree)} onPress={() => onContinue(agree, face)}>
          {t.continue}
        </Button>
      </div>
    </Card>
  );
}

/** S02: only the methods the server offered; one that needs another device is reached through a one-use link and its QR code (S06, S12). */
function Choose({
  methods,
  withheld,
  t,
  heading,
  busy,
  cred,
  onChoose,
}: {
  methods: Method[];
  withheld: Context["methods_withheld"];
  t: Messages;
  heading: HeadingRef;
  busy: boolean;
  cred: Credential;
  onChoose: (m: Method) => void;
}) {
  const [link, setLink] = useState<{ url: string; minutes: number } | null>(null);
  async function handoff() {
    const h = await api.handoff(cred);
    setLink({ url: h.handoff_url, minutes: Math.max(1, Math.round((new Date(h.expires_at).getTime() - Date.now()) / 60000)) });
  }
  return (
    <Card>
      <h1 ref={heading} tabIndex={-1} id="flow-h" className="text-xl font-bold text-ink">
        {t.chooseTitle}
      </h1>
      <ul className="mt-3 space-y-3">
        {methods.map((m) => (
          <li key={m.method} className="rounded border border-border p-3">
            <p className="font-medium">{t.kind[m.kind] ?? m.kind}</p>
            {m.requires_handoff ? (
              <>
                <p className="text-sm text-ink-muted">{t.cannotHere}</p>
                <Button variant="secondary" isDisabled={busy} onPress={() => handoff().catch(() => undefined)}>
                  {t.getPhoneLink}
                </Button>
              </>
            ) : (
              <Button isDisabled={busy} onPress={() => onChoose(m)}>
                {t.useThis}
              </Button>
            )}
          </li>
        ))}
      </ul>
      {link ? (
        <Banner tone="info" title={t.phoneLinkTitle(link.minutes)}>
          <span className="mt-2 block">
            <QrCode text={link.url} label={t.phoneLinkQr} />
          </span>
          <span className="mt-2 block break-all" dir="ltr">
            {link.url}
          </span>
          <span className="mt-2 block">{t.phoneLinkBody}</span>
        </Banner>
      ) : null}
      {withheld.length ? (
        <details className="mt-4 text-sm">
          <summary>{t.whyUnavailable}</summary>
          <ul className="mt-2 list-disc ps-5">
            {withheld.map((w) => (
              <li key={w.method}>{t.withheld[w.reason] ?? t.notOffered}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </Card>
  );
}

/** S04 to S07: the document photograph, typed details on the automatic route, and a live photograph on the face route. "Captured" is never "verified". */
function Capture({
  ctx,
  t,
  status,
  heading,
  busy,
  cred,
  onSent,
  onError,
}: {
  ctx: Context;
  t: Messages;
  status: Status;
  heading: HeadingRef;
  busy: boolean;
  cred: Credential;
  onSent: (s: Status) => void;
  onError: (e: FlowError | null) => void;
}) {
  const method = ctx.methods.find((m) => m.method === status.method);
  const face = Boolean(method?.biometric);
  const typed = method?.kind === "DOCUMENT_AUTOMATIC";
  const [doc, setDoc] = useState<File | null>(null);
  const [live, setLive] = useState<File | null>(null);
  const [declared, setDeclared] = useState<Declared>({ surname: "", given_names: "", document_number: "", birth_date: "" });
  const [sending, setSending] = useState(false);
  const ready = Boolean(doc) && (!face || Boolean(live)) && (!typed || declaredComplete(declared));
  async function send() {
    if (!doc || !ready) return;
    setSending(true);
    onError(null);
    try {
      const { nonce } = await api.intent(cred, "DOCUMENT");
      onSent(
        await api.document(cred, {
          nonce,
          captured_at: new Date().toISOString(),
          image_base64: await fileToBase64(doc),
          ...(face && live ? { selfie_base64: await fileToBase64(live), consent: true } : {}),
          ...(typed ? { declared } : {}),
        }),
      );
      setDoc(null);
      setLive(null);
    } catch (e) {
      onError(e instanceof FlowError ? e : new FlowError("ERROR", t.somethingWrong, 0));
    } finally {
      setSending(false);
    }
  }
  const fields = [
    ["surname", t.surname, "text"],
    ["given_names", t.givenNames, "text"],
    ["document_number", t.documentNumber, "text"],
    ["birth_date", t.birthDate, "date"],
  ] as const;
  return (
    <Card>
      <h1 ref={heading} tabIndex={-1} id="flow-h" className="text-xl font-bold text-ink">
        {t.captureTitle}
      </h1>
      <p className="mt-2 text-sm text-ink-muted">{t.captureHint}</p>
      <div className="mt-3 space-y-2">
        <DocumentCapture onCaptured={setDoc} disabled={busy || sending} />
        <label className="block text-sm">
          {t.chooseFile} <input type="file" accept="image/jpeg,image/png" onChange={(e) => setDoc(e.target.files?.[0] ?? null)} />
        </label>
        {doc ? <p role="status">{t.documentReady}</p> : null}
      </div>
      {typed ? (
        <fieldset className="mt-4 space-y-2">
          <legend className="font-semibold">{t.detailsTitle}</legend>
          <p className="text-sm text-ink-muted">{t.detailsHint}</p>
          {fields.map(([key, label, type]) => (
            <label key={key} className="block text-sm">
              {label}
              <input
                type={type}
                className="mt-1 block w-full rounded border border-border px-2 py-1"
                value={declared[key]}
                onChange={(e) => setDeclared({ ...declared, [key]: e.target.value })}
              />
            </label>
          ))}
        </fieldset>
      ) : null}
      {face ? (
        <div className="mt-4 space-y-2">
          <h2 className="font-semibold">{t.liveTitle}</h2>
          <p className="text-sm text-ink-muted">{t.liveHint}</p>
          <LiveCapture onCaptured={setLive} disabled={busy || sending} />
          {live ? <p role="status">{t.liveReady}</p> : null}
        </div>
      ) : null}
      <div className="mt-4">
        <Button isDisabled={!ready || sending} onPress={send}>
          {sending ? t.sending : t.sendPhotos}
        </Button>
      </div>
    </Card>
  );
}
