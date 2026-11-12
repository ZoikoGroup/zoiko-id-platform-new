/**
 * The browser verification widget: `mount(container, options)` (ZID-INT-002 section 05, `session.mount`).
 *
 * A thin, accessible view over the session state machine in `session.ts`. It collects nothing before consent, does not decide anything
 * itself, and never says a person is verified: the host's own backend talks to Zoiko iD and reports back through the two callbacks.
 *
 *  - `host.capture(file)` sends the chosen file to the host's server (which uploads it) and answers whether the payload was intact;
 *  - `host.awaitSource()` resolves with what the source answered (`CONFIRMED`, `UNAVAILABLE`, `UNCLEAR`, `TIMED_OUT`).
 *
 * Accessibility: a labelled region, one heading that takes focus whenever the step changes, a polite live region for status, errors in
 * an `alert`, native buttons and a native file input with a visible label (so keyboard and screen readers work with no extra code),
 * and consent that can be withdrawn while capture is open. All text goes in as text, never as markup. No styles, fonts, scripts or
 * network calls of its own: the host styles the `zid-*` classes. Wording is in English, Spanish and French; Spanish and French are
 * machine drafts awaiting human review and are marked as such (`widgetWording(locale).reviewed`).
 */

import { afterCapture, afterSource, move, withdrawConsent, type SessionEvent, type SessionState } from "./session.js";

export type SourceAnswer = "CONFIRMED" | "UNAVAILABLE" | "UNCLEAR" | "TIMED_OUT";

export type WidgetHost = {
  capture(file: File): Promise<{ intact: boolean }>;
  awaitSource(): Promise<SourceAnswer>;
};

export type WidgetOptions = {
  host: WidgetHost;
  locale?: string;
  /** What the person is agreeing to, in the host's own words. Required: nothing is captured without it. */
  consentText: string;
  /** Called with each documented session event and the new state. */
  onEvent?: (event: SessionEvent, state: SessionState) => void;
  accept?: string;
  heading?: string;
};

export type WidgetHandle = {
  state(): SessionState;
  destroy(): void;
};

type Words = {
  reviewed: boolean;
  heading: string;
  consentLead: string;
  agree: string;
  decline: string;
  choose: string;
  send: string;
  working: string;
  retry: string;
  withdraw: string;
  states: Partial<Record<SessionState, string>>;
  retryNote: string;
  captureError: string;
};

const EN: Words = {
  reviewed: true,
  heading: "Check your document",
  consentLead: "Before anything is collected, please read and agree:",
  agree: "I agree",
  decline: "No thanks",
  choose: "Choose a document",
  send: "Send document",
  working: "Working on it",
  retry: "Try again",
  withdraw: "Withdraw my consent",
  retryNote: "The file could not be read. Nothing was decided. Please choose it again.",
  captureError: "The document could not be sent. Please try again.",
  states: {
    READY: "Thank you. You can choose a document now.",
    COLLECTING: "Choose the document you want checked.",
    RETRY_REQUIRED: "The file could not be read. Nothing was decided. Please choose it again.",
    EVIDENCE_RECEIVED: "We received your document.",
    SOURCE_PENDING: "We are waiting for the issuer to answer. This is not a refusal. You can close this page and come back.",
    REVIEW_REQUIRED: "A person needs to look at this. Nothing has been decided yet.",
    COMPLETE: "The check is finished.",
    EXPIRED: "This check ran out of time before an answer arrived. Nothing was decided. You can start again.",
    DECLINED: "You chose not to continue. Nothing was collected.",
    CANCELED: "Stopped. You withdrew your consent and nothing more will be collected.",
    UNAVAILABLE: "This check is not available right now. Please try later.",
  },
};

const ES: Words = {
  reviewed: false,
  heading: "Compruebe su documento",
  consentLead: "Antes de recoger nada, lea y acepte:",
  agree: "Acepto",
  decline: "No, gracias",
  choose: "Elija un documento",
  send: "Enviar documento",
  working: "Trabajando en ello",
  retry: "Intentar de nuevo",
  withdraw: "Retirar mi consentimiento",
  retryNote: "No se pudo leer el archivo. No se decidió nada. Elíjalo de nuevo.",
  captureError: "No se pudo enviar el documento. Inténtelo de nuevo.",
  states: {
    READY: "Gracias. Ya puede elegir un documento.",
    COLLECTING: "Elija el documento que quiere comprobar.",
    RETRY_REQUIRED: "No se pudo leer el archivo. No se decidió nada. Elíjalo de nuevo.",
    EVIDENCE_RECEIVED: "Hemos recibido su documento.",
    SOURCE_PENDING: "Esperamos la respuesta del emisor. No es un rechazo. Puede cerrar esta página y volver.",
    REVIEW_REQUIRED: "Una persona debe revisar esto. Aún no se ha decidido nada.",
    COMPLETE: "La comprobación ha terminado.",
    EXPIRED: "La comprobación se quedó sin tiempo antes de recibir respuesta. No se decidió nada. Puede empezar de nuevo.",
    DECLINED: "Decidió no continuar. No se recogió nada.",
    CANCELED: "Detenido. Retiró su consentimiento y no se recogerá nada más.",
    UNAVAILABLE: "Esta comprobación no está disponible ahora. Inténtelo más tarde.",
  },
};

const FR: Words = {
  reviewed: false,
  heading: "Vérifiez votre document",
  consentLead: "Avant toute collecte, lisez et acceptez :",
  agree: "J'accepte",
  decline: "Non merci",
  choose: "Choisissez un document",
  send: "Envoyer le document",
  working: "En cours",
  retry: "Réessayer",
  withdraw: "Retirer mon consentement",
  retryNote: "Le fichier n'a pas pu être lu. Rien n'a été décidé. Veuillez le choisir à nouveau.",
  captureError: "Le document n'a pas pu être envoyé. Veuillez réessayer.",
  states: {
    READY: "Merci. Vous pouvez choisir un document.",
    COLLECTING: "Choisissez le document à vérifier.",
    RETRY_REQUIRED: "Le fichier n'a pas pu être lu. Rien n'a été décidé. Veuillez le choisir à nouveau.",
    EVIDENCE_RECEIVED: "Nous avons reçu votre document.",
    SOURCE_PENDING: "Nous attendons la réponse de l'émetteur. Ce n'est pas un refus. Vous pouvez fermer cette page et revenir.",
    REVIEW_REQUIRED: "Une personne doit examiner ceci. Rien n'a encore été décidé.",
    COMPLETE: "La vérification est terminée.",
    EXPIRED: "La vérification a expiré avant l'arrivée d'une réponse. Rien n'a été décidé. Vous pouvez recommencer.",
    DECLINED: "Vous avez choisi de ne pas continuer. Rien n'a été collecté.",
    CANCELED: "Arrêté. Vous avez retiré votre consentement et rien d'autre ne sera collecté.",
    UNAVAILABLE: "Cette vérification n'est pas disponible pour le moment. Veuillez réessayer plus tard.",
  },
};

const WORDING: Record<string, Words> = { en: EN, es: ES, fr: FR };

/** The wording for a locale, falling back to English for any language that is not offered (never a blank label). */
export function widgetWording(locale?: string): Words {
  const base = (locale ?? "en").toLowerCase().split(/[-_]/)[0] ?? "en";
  return WORDING[base] ?? EN;
}

let counter = 0;

function el<K extends keyof HTMLElementTagNameMap>(
  doc: Document,
  tag: K,
  attrs: Record<string, string> = {},
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = doc.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

export function mount(container: HTMLElement, options: WidgetOptions): WidgetHandle {
  if (!options.consentText.trim()) throw new Error("consentText is required: nothing is collected without consent");
  const doc = container.ownerDocument;
  const words = widgetWording(options.locale);
  const id = `zid-widget-${++counter}`;
  let state: SessionState = "NEW";
  let busy = false;
  let alive = true;

  const root = el(doc, "section", { class: "zid-widget", role: "region", "aria-labelledby": `${id}-h`, lang: (options.locale ?? "en").split(/[-_]/)[0] ?? "en" });
  const heading = el(doc, "h2", { id: `${id}-h`, class: "zid-heading", tabindex: "-1" }, options.heading ?? words.heading);
  const status = el(doc, "p", { class: "zid-status", role: "status", "aria-live": "polite" });
  const alert = el(doc, "p", { class: "zid-error", role: "alert" });
  const body = el(doc, "div", { class: "zid-body" });
  root.append(heading, status, alert, body);
  container.append(root);

  const emit = (event: SessionEvent | null) => {
    if (event) options.onEvent?.(event, state);
  };
  const go = (to: SessionState, decidedBy?: string) => {
    const t = move(state, to, decidedBy ? { decidedBy } : {});
    state = t.to;
    emit(t.event);
  };

  function button(label: string, onClick: () => void, extra: Record<string, string> = {}): HTMLButtonElement {
    const b = el(doc, "button", { type: "button", class: "zid-button", ...extra }, label);
    b.addEventListener("click", () => {
      if (!busy && alive) onClick();
    });
    return b;
  }

  function render(focus = true) {
    if (!alive) return;
    body.replaceChildren();
    alert.textContent = "";
    status.textContent = words.states[state] ?? "";
    if (state === "CONSENT_REQUIRED") {
      body.append(
        el(doc, "p", { class: "zid-consent-lead" }, words.consentLead),
        el(doc, "p", { class: "zid-consent" }, options.consentText),
        button(words.agree, () => {
          go("READY");
          go("COLLECTING");
          render();
        }, { "data-zid": "agree" }),
        button(words.decline, () => {
          go("DECLINED");
          render();
        }, { "data-zid": "decline" }),
      );
    } else if (state === "COLLECTING" || state === "RETRY_REQUIRED") {
      if (state === "RETRY_REQUIRED") alert.textContent = words.retryNote;
      const input = el(doc, "input", { type: "file", id: `${id}-file`, class: "zid-file", ...(options.accept ? { accept: options.accept } : {}) });
      const label = el(doc, "label", { for: `${id}-file`, class: "zid-label" }, words.choose);
      body.append(
        label,
        input,
        button(state === "RETRY_REQUIRED" ? words.retry : words.send, () => void submit(input), { "data-zid": "send" }),
        button(words.withdraw, () => {
          const t = withdrawConsent(state);
          state = t.to;
          emit(t.event);
          render();
        }, { "data-zid": "withdraw" }),
      );
    }
    if (focus) heading.focus();
  }

  async function submit(input: HTMLInputElement) {
    const file = input.files?.[0];
    if (!file) {
      alert.textContent = words.choose;
      input.focus();
      return;
    }
    busy = true;
    status.textContent = words.working;
    root.setAttribute("aria-busy", "true");
    try {
      const sent = await options.host.capture(file);
      if (!alive) return;
      if (state === "RETRY_REQUIRED") go("COLLECTING");
      const after = afterCapture(sent.intact);
      go(after);
      if (after === "RETRY_REQUIRED") return;
      go("SOURCE_PENDING");
      render(false);
      const answer = await options.host.awaitSource();
      if (!alive) return;
      const next = afterSource(answer);
      if (next !== state) go(next);
      render(true);
    } catch {
      if (!alive) return;
      // a failed send is not a decision: the person can try again, and the state does not move on
      if (state === "COLLECTING" || state === "RETRY_REQUIRED") alert.textContent = words.captureError;
      else render(true);
    } finally {
      busy = false;
      root.removeAttribute("aria-busy");
      if (alive && (state === "RETRY_REQUIRED" || state === "EVIDENCE_RECEIVED")) render(true);
    }
  }

  go("CONSENT_REQUIRED");
  render();
  return {
    state: () => state,
    destroy() {
      alive = false;
      root.remove();
    },
  };
}
